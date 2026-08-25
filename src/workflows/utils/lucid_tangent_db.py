"""Project LUCID's frozen, deterministic TangentsDB contract.

This module intentionally has no LLM or carrier-generation code.  It validates
reply-expressible intents and selects a maximally separated codebook from a
candidate pool; later orchestration can persist the resulting artifact.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from pydantic import BaseModel, Field, validate_call

from workflows.utils.naturalness_gate import tokenize_content_words
from workflows.utils.protocol_utils import stable_hash

LUCID_TANGENTS_DB_SCHEMA_VERSION = 1
LUCID_TANGENTS_DB_NAMESPACE = "project_lucid/tangents_db/v1"
_GENERIC_TOKENS = {"analysis", "aspect", "discussion", "issue", "perspective", "topic"}


class LucidTangentCandidate(BaseModel):
    """One compact, thread-grounded intent supplied to LUCID selection."""

    tangent_id: str
    subject: str
    relation: str
    thread_cue: str
    source_quote: str
    source_document: int = 0
    category: str = ""

    def intent_text(self) -> str:
        return " ".join((self.subject, self.relation, self.thread_cue))

    def to_angle(self) -> dict[str, Any]:
        """Receiver-compatible angle dict; keeps LUCID intent for provenance."""
        return {
            "source_quote": self.source_quote,
            "tangent": self.intent_text(),
            "category": self.category,
            "source_document": self.source_document,
            "lucid_tangent_id": self.tangent_id,
            "lucid_intent": {
                "subject": self.subject,
                "relation": self.relation,
                "thread_cue": self.thread_cue,
            },
        }


class LucidCandidateScore(BaseModel):
    """Deterministic admission result retained for candidate provenance."""

    tangent_id: str
    post_grounding: float
    parent_grounding: float
    reply_expressible: bool
    generic: bool
    accepted: bool
    rejection_reasons: list[str] = Field(default_factory=list)


class LucidTangentDbArtifact(BaseModel):
    """Frozen LUCID codebook and the provenance needed to reproduce it."""

    schema_version: int = LUCID_TANGENTS_DB_SCHEMA_VERSION
    artifact_namespace: str = LUCID_TANGENTS_DB_NAMESPACE
    post_id: str
    selected_parent_id: str | None = None
    post_context_hash: str
    parent_context_hash: str
    generation_input_hash: str
    candidates: list[LucidTangentCandidate]
    candidate_scores: list[LucidCandidateScore]
    selected_tangent_ids: list[str]
    pairwise_separation: dict[str, float]
    content_hash: str = ""


class LucidSelectionResult(BaseModel):
    """Pure selection result, ready to be persisted as a frozen artifact."""

    artifact: LucidTangentDbArtifact
    selected: list[LucidTangentCandidate]


def _tokens(text: str) -> set[str]:
    return set(tokenize_content_words(text))


def _overlap_fraction(intent: set[str], context: set[str]) -> float:
    return len(intent & context) / len(intent) if intent else 0.0


def _is_reply_expressible(candidate: LucidTangentCandidate) -> bool:
    return all(len(_tokens(value)) > 0 for value in (candidate.subject, candidate.relation, candidate.thread_cue))


def _is_generic(candidate: LucidTangentCandidate) -> bool:
    intent = _tokens(candidate.intent_text())
    return not intent or intent <= _GENERIC_TOKENS


def _score_candidate(
    candidate: LucidTangentCandidate, post_tokens: set[str], parent_tokens: set[str]
) -> LucidCandidateScore:
    intent = _tokens(candidate.intent_text())
    post_grounding = _overlap_fraction(intent, post_tokens)
    parent_grounding = _overlap_fraction(_tokens(candidate.thread_cue), parent_tokens)
    expressible = _is_reply_expressible(candidate)
    generic = _is_generic(candidate)
    reasons = _rejection_reasons(post_grounding, parent_grounding, expressible, generic)
    return LucidCandidateScore(
        tangent_id=candidate.tangent_id, post_grounding=post_grounding,
        parent_grounding=parent_grounding, reply_expressible=expressible,
        generic=generic, accepted=not reasons, rejection_reasons=reasons,
    )


def _rejection_reasons(post: float, parent: float, expressible: bool, generic: bool) -> list[str]:
    reasons: list[str] = []
    if post == 0.0:
        reasons.append("not_post_grounded")
    if parent == 0.0:
        reasons.append("not_parent_grounded")
    if not expressible:
        reasons.append("not_reply_expressible")
    if generic:
        reasons.append("generic_intent")
    return reasons


def _similarity(left: LucidTangentCandidate, right: LucidTangentCandidate) -> float:
    left_tokens, right_tokens = _tokens(left.intent_text()), _tokens(right.intent_text())
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 1.0


def _select_codebook(candidates: list[LucidTangentCandidate], size: int) -> list[LucidTangentCandidate]:
    if size <= 0 or not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.tangent_id)
    selected = [ordered.pop(0)]
    while ordered and len(selected) < size:
        best = min(ordered, key=lambda item: (max(_similarity(item, kept) for kept in selected), item.tangent_id))
        selected.append(best)
        ordered.remove(best)
    return selected


def _pairwise_separation(selected: list[LucidTangentCandidate]) -> dict[str, float]:
    scores = [1.0 - _similarity(left, right) for left, right in combinations(selected, 2)]
    return {"minimum": min(scores) if scores else 1.0, "mean": sum(scores) / len(scores) if scores else 1.0}


def _artifact_hash_payload(artifact: LucidTangentDbArtifact) -> dict[str, Any]:
    return artifact.model_dump(mode="json", exclude={"content_hash"})


def _split_intent_spans(text: str) -> tuple[str, str, str]:
    tokens = tokenize_content_words(text)
    if len(tokens) < 2:
        return text.strip(), "", ""
    first = max(1, len(tokens) // 3)
    second = max(first + 1, (2 * len(tokens)) // 3)
    return " ".join(tokens[:first]), " ".join(tokens[first:second]), " ".join(tokens[second:])


def _thread_cue_from_quote(source_quote: str, parent_tokens: set[str], fallback: str) -> str:
    quote_tokens = [token for token in tokenize_content_words(source_quote) if token in parent_tokens]
    if quote_tokens:
        return " ".join(quote_tokens)
    return fallback.strip()


@validate_call
def project_legacy_angle_to_lucid_candidate(
    angle: dict[str, Any], *, tangent_id: str, parent_context: str = ""
) -> LucidTangentCandidate:
    """Lossy projection: academic/generic tangents should fail later admission checks."""
    source_quote = str(angle.get("source_quote") or "").strip()
    tangent = str(angle.get("tangent") or source_quote).strip()
    subject, relation, residual = _split_intent_spans(tangent)
    cue = _thread_cue_from_quote(source_quote, _tokens(parent_context), residual or source_quote)
    return LucidTangentCandidate(
        tangent_id=tangent_id,
        subject=subject,
        relation=relation,
        thread_cue=cue,
        source_quote=source_quote or tangent,
        source_document=int(angle.get("source_document", 0) or 0),
        category=str(angle.get("category") or ""),
    )


def post_context_text(post: dict[str, Any]) -> str:
    return " ".join(
        str(post.get(key) or "").strip() for key in ("title", "selftext") if str(post.get(key) or "").strip()
    )


def parent_context_text(post: dict[str, Any]) -> str:
    comments = post.get("comments")
    if not isinstance(comments, list):
        return ""
    bodies = [
        " ".join(str(item.get("body") or "").split())
        for item in comments
        if isinstance(item, dict) and str(item.get("body") or "").strip()
    ]
    return " ".join(bodies)


@validate_call
def build_lucid_tangents_db(
    *, post_id: str, post_context: str, parent_context: str, candidates: list[LucidTangentCandidate], size: int
) -> LucidSelectionResult:
    """Score candidates, reject invalid intents, and select a deterministic codebook."""
    post_tokens, parent_tokens = _tokens(post_context), _tokens(parent_context)
    scores = [_score_candidate(item, post_tokens, parent_tokens) for item in candidates]
    accepted = [item for item, score in zip(candidates, scores, strict=True) if score.accepted]
    selected = _select_codebook(accepted, size)
    artifact = LucidTangentDbArtifact(
        post_id=post_id, post_context_hash=stable_hash(post_context),
        parent_context_hash=stable_hash(parent_context), generation_input_hash=stable_hash(candidates),
        candidates=candidates, candidate_scores=scores,
        selected_tangent_ids=[item.tangent_id for item in selected],
        pairwise_separation=_pairwise_separation(selected),
    )
    artifact.content_hash = stable_hash(_artifact_hash_payload(artifact))
    return LucidSelectionResult(artifact=artifact, selected=selected)
