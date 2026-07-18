"""Deterministic tangent-DB builder: relevance-anchored, distinctness-aware angle selection.

Pure functions only (input -> output; no I/O, no wall-clock, no RNG). The receiver must be
able to reproduce the identical DB from the persisted post alone, so every ordering decision
here is deterministic. Phase 0 runs this builder only as a shadow report behind
``WORKFLOW_TANGENT_DB_BUILDER=v1``; emitted legacy angles remain unchanged.

Pipeline: Stage A (relevance anchor) -> Stage B (Jaccard distinctness) -> Stage C (capacity
reconciliation with an optional similarity-relaxing floor). See
``docs/plans/naturalness-overhaul/tangent-db-revamp.md``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field, validate_call

from workflows.utils.naturalness_gate import (
    context_texts_from_post,
    tokenize_content_words,
)
from workflows.utils.protocol_utils import stable_hash

# Category strings emitted by ``gen_angles._source_category`` mapped back to their raw source.
_SOURCE_BY_CATEGORY = {
    "original post": "post",
    "community discussion": "comments",
    "reference material": "search_results",
}
# Higher priority = preferred on relevance ties; keeps ordering stable and receiver-reproducible.
_SOURCE_PRIORITY = {"post": 3, "comments": 2, "search_results": 1}


class AngleCandidate(BaseModel):
    """One raw extractive angle before DB selection (mirrors the legacy angle dict fields)."""

    source_quote: str = ""
    tangent: str = ""
    category: str = ""
    source_document: int = 0
    source: str = ""

    @classmethod
    def from_angle(cls, angle: dict[str, Any]) -> AngleCandidate:
        return cls(
            source_quote=str(angle.get("source_quote", "")),
            tangent=str(angle.get("tangent", "")),
            category=str(angle.get("category", "")),
            source_document=int(angle.get("source_document", 0) or 0),
            source=str(angle.get("source", "")),
        )

    def to_angle(self) -> dict[str, Any]:
        return {
            "source_quote": self.source_quote,
            "tangent": self.tangent,
            "category": self.category,
            "source_document": self.source_document,
        }


class PostContext(BaseModel):
    """Anchor text a human reader actually sees in-thread (title + selftext + comment bodies)."""

    anchor_texts: list[str] = Field(default_factory=list)

    @classmethod
    def from_post(cls, post: dict[str, Any]) -> PostContext:
        return cls(anchor_texts=context_texts_from_post(post))


class TangentDbConfig(BaseModel):
    """Effective, hashable knobs for one DB build (reproduced by the receiver for parity)."""

    builder_version: str = "tangent_db_v1"
    min_relevance: float = 0.12
    search_relevance_mult: float = 1.5
    max_similarity: float = 0.7
    min_size: int = 0
    max_output: int = 12
    use_idf: bool = False
    semantic_dedup: bool = False

    def signature(self) -> dict[str, Any]:
        return {
            "builder_version": self.builder_version,
            "min_relevance": round(self.min_relevance, 6),
            "search_relevance_mult": round(self.search_relevance_mult, 6),
            "max_similarity": round(self.max_similarity, 6),
            "min_size": self.min_size,
            "max_output": self.max_output,
            "use_idf": self.use_idf,
            "semantic_dedup": self.semantic_dedup,
        }

    def config_hash(self) -> str:
        return stable_hash(self.signature())


class ScoredCandidate(BaseModel):
    """A candidate annotated with its source class, relevance, and content-token set."""

    candidate: AngleCandidate
    idx: int
    source: str
    source_priority: int
    relevance: float
    tokens: list[str]


class TangentDbReport(BaseModel):
    """Observability + audit payload persisted alongside the angles (see plan section 3.5)."""

    builder_version: str
    input_candidate_count: int
    kept_count: int
    dropped: dict[str, int]
    relevance: dict[str, Any]
    distinctness: dict[str, float]
    source_mix_kept: dict[str, int]
    config: dict[str, Any]
    config_hash: str
    relaxations: list[dict[str, Any]] = Field(default_factory=list)


class TangentDbResult(BaseModel):
    """Ordered kept angles (legacy dict shape) plus the build report."""

    angles: list[dict[str, Any]]
    report: TangentDbReport


def _candidate_source(cand: AngleCandidate) -> str:
    if cand.source:
        return cand.source
    return _SOURCE_BY_CATEGORY.get(cand.category.strip().lower(), "search_results")


def _candidate_tokens(cand: AngleCandidate) -> list[str]:
    return sorted(set(tokenize_content_words(f"{cand.source_quote} {cand.tangent}")))


def _anchor_weights(anchor_texts: list[str], *, use_idf: bool) -> dict[str, float]:
    counts = Counter(tokenize_content_words(" ".join(anchor_texts)))
    if not use_idf:
        return {token: 1.0 for token in counts}
    return {token: 1.0 / float(freq) for token, freq in counts.items()}


def _relevance_score(tokens: list[str], weights: dict[str, float]) -> float:
    """Fraction of a candidate's content tokens present in the thread anchor (0..1)."""
    if not tokens:
        return 0.0
    overlap = sum(weights.get(token, 0.0) for token in tokens)
    return overlap / float(len(tokens))


def _threshold_for(source: str, cfg: TangentDbConfig) -> float:
    mult = cfg.search_relevance_mult if source == "search_results" else 1.0
    return cfg.min_relevance * mult


def _score_candidates(
    candidates: list[AngleCandidate], ctx: PostContext, cfg: TangentDbConfig
) -> list[ScoredCandidate]:
    weights = _anchor_weights(ctx.anchor_texts, use_idf=cfg.use_idf)
    scored: list[ScoredCandidate] = []
    for idx, cand in enumerate(candidates):
        source = _candidate_source(cand)
        tokens = _candidate_tokens(cand)
        scored.append(
            ScoredCandidate(
                candidate=cand,
                idx=idx,
                source=source,
                source_priority=_SOURCE_PRIORITY.get(source, 1),
                relevance=_relevance_score(tokens, weights),
                tokens=tokens,
            )
        )
    return scored


def _admit_relevant(
    scored: list[ScoredCandidate], cfg: TangentDbConfig
) -> tuple[list[ScoredCandidate], int]:
    admitted = [s for s in scored if s.relevance >= _threshold_for(s.source, cfg)]
    admitted.sort(key=lambda s: (-s.relevance, -s.source_priority, s.idx))
    return admitted, len(scored) - len(admitted)


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    union = len(sa | sb)
    return len(sa & sb) / union if union else 0.0


def _dedupe(
    admitted: list[ScoredCandidate], max_similarity: float
) -> tuple[list[ScoredCandidate], int]:
    kept: list[ScoredCandidate] = []
    dropped = 0
    for cand in admitted:
        if any(_jaccard(cand.tokens, k.tokens) > max_similarity for k in kept):
            dropped += 1
            continue
        kept.append(cand)
    return kept, dropped


def _apply_capacity(
    admitted: list[ScoredCandidate], cfg: TangentDbConfig
) -> tuple[list[ScoredCandidate], dict[str, int], list[dict[str, Any]]]:
    """Dedupe, optionally relax similarity to hit ``min_size`` (never relevance), then cap."""
    max_sim = cfg.max_similarity
    relaxations: list[dict[str, Any]] = []
    kept, near_dup = _dedupe(admitted, max_sim)
    while cfg.min_size and len(kept) < cfg.min_size and max_sim < 0.95:
        max_sim = round(min(0.95, max_sim + 0.05), 6)
        kept, near_dup = _dedupe(admitted, max_sim)
        relaxations.append({"max_similarity": max_sim, "kept_count": len(kept)})
    capped = 0
    if len(kept) > cfg.max_output:
        capped = len(kept) - cfg.max_output
        kept = kept[: cfg.max_output]
    return kept, {"near_duplicate": near_dup, "capped": capped}, relaxations


def _relevance_stats(kept: list[ScoredCandidate], threshold: float) -> dict[str, Any]:
    """Persist the exact kept-score distribution so later summaries need no raw angles."""
    scores = sorted(round(s.relevance, 6) for s in kept)
    if not scores:
        return {
            "min": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "max": 0.0,
            "threshold": round(threshold, 6),
            "scores_kept": [],
        }
    middle = len(scores) // 2
    median = scores[middle] if len(scores) % 2 else (scores[middle - 1] + scores[middle]) / 2
    return {
        "min": round(scores[0], 6),
        "mean": round(sum(scores) / len(scores), 6),
        "median": round(median, 6),
        "max": round(scores[-1], 6),
        "threshold": round(threshold, 6),
        "scores_kept": scores,
    }


def _mean_pairwise_jaccard(kept: list[ScoredCandidate]) -> float:
    if len(kept) < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            total += _jaccard(kept[i].tokens, kept[j].tokens)
            pairs += 1
    return round(total / pairs, 6) if pairs else 0.0


def _source_mix(kept: list[ScoredCandidate]) -> dict[str, int]:
    mix = {"post": 0, "comments": 0, "search_results": 0}
    for s in kept:
        mix[s.source] = mix.get(s.source, 0) + 1
    return mix


@validate_call
def build_tangent_db(
    candidates: list[AngleCandidate], ctx: PostContext, cfg: TangentDbConfig
) -> TangentDbResult:
    """Select relevant, distinct tangents deterministically; return kept angles + report."""
    scored = _score_candidates(candidates, ctx, cfg)
    admitted, low_relevance = _admit_relevant(scored, cfg)
    kept, drop_counts, relaxations = _apply_capacity(admitted, cfg)
    report = TangentDbReport(
        builder_version=cfg.builder_version,
        input_candidate_count=len(candidates),
        kept_count=len(kept),
        dropped={"low_thread_relevance": low_relevance, **drop_counts},
        relevance=_relevance_stats(kept, cfg.min_relevance),
        distinctness={
            "mean_pairwise_jaccard": _mean_pairwise_jaccard(kept),
            "max_similarity": cfg.max_similarity,
        },
        source_mix_kept=_source_mix(kept),
        config=cfg.signature(),
        config_hash=cfg.config_hash(),
        relaxations=relaxations,
    )
    return TangentDbResult(angles=[s.candidate.to_angle() for s in kept], report=report)


def tangent_db_config_from_env(max_output: int) -> TangentDbConfig:
    """Build a config from the effective ``WORKFLOW_TANGENT_DB_*`` env knobs (thin adapter)."""
    from infrastructure.config import (
        get_workflow_tangent_db_max_similarity,
        get_workflow_tangent_db_min_relevance,
        get_workflow_tangent_db_min_size,
        get_workflow_tangent_db_search_relevance_mult,
        get_workflow_tangent_db_semantic_dedup,
    )

    return TangentDbConfig(
        min_relevance=get_workflow_tangent_db_min_relevance(),
        search_relevance_mult=get_workflow_tangent_db_search_relevance_mult(),
        max_similarity=get_workflow_tangent_db_max_similarity(),
        min_size=get_workflow_tangent_db_min_size(),
        max_output=max_output,
        semantic_dedup=get_workflow_tangent_db_semantic_dedup(),
    )
