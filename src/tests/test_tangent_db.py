"""Tests for the deterministic tangent-DB builder (plan 2: tangent-db-revamp, Phase 0)."""

from __future__ import annotations

import pytest

from infrastructure.config import get_workflow_tangent_db_builder
from workflows.utils.tangent_db import (
    AngleCandidate,
    PostContext,
    TangentDbConfig,
    build_tangent_db,
)

# A flood/rescue thread. Anchor tokens include: flood, camp, bounty, rescue, missing, river.
_POST = {
    "title": "Flash flood at the summer camp leaves several missing",
    "selftext": "Rescue teams searched the river all night; a reward bounty was offered for help.",
    "comments": [
        {"id": "c1", "body": "The flood swept through the camp so fast, terrifying for the kids."},
        {"id": "c2", "body": "Any word on the missing hikers near the river bounty?"},
    ],
}


def _cand(source_quote: str, category: str, source_document: int = 0) -> AngleCandidate:
    return AngleCandidate(
        source_quote=source_quote,
        tangent=source_quote,
        category=category,
        source_document=source_document,
    )


def _relevant_candidates() -> list[AngleCandidate]:
    return [
        _cand("The flood swept through the camp so fast this morning.", "Community Discussion"),
        _cand("Rescue teams searched the river for the missing campers.", "Original Post"),
    ]


def _default_cfg(**overrides: object) -> TangentDbConfig:
    base = {"min_relevance": 0.12, "max_similarity": 0.7, "max_output": 12}
    base.update(overrides)
    return TangentDbConfig(**base)  # type: ignore[arg-type]


def test_builder_defaults_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKFLOW_TANGENT_DB_BUILDER", raising=False)
    assert get_workflow_tangent_db_builder() == "legacy"
    monkeypatch.setenv("WORKFLOW_TANGENT_DB_BUILDER", "V1")
    assert get_workflow_tangent_db_builder() == "v1"
    monkeypatch.setenv("WORKFLOW_TANGENT_DB_BUILDER", "nonsense")
    assert get_workflow_tangent_db_builder() == "legacy"


def test_determinism_same_input_same_output() -> None:
    ctx = PostContext.from_post(_POST)
    cfg = _default_cfg()
    candidates = _relevant_candidates()
    first = build_tangent_db(candidates, ctx, cfg)
    second = build_tangent_db(candidates, ctx, cfg)
    assert first.model_dump() == second.model_dump()


def test_offtopic_search_doc_is_dropped_under_v1() -> None:
    # A fluent but off-thread sentence: zero content-token overlap with the flood thread.
    offtopic = _cand(
        "Competitive dynamics among major coffee retailers pressured people this quarter.",
        "Reference Material",
    )
    ctx = PostContext.from_post(_POST)
    cfg = _default_cfg()
    result = build_tangent_db([*_relevant_candidates(), offtopic], ctx, cfg)
    kept_quotes = [a["source_quote"] for a in result.angles]
    assert offtopic.source_quote not in kept_quotes
    assert result.report.dropped["low_thread_relevance"] >= 1
    assert len(kept_quotes) == 2


def test_search_doc_faces_a_higher_relevance_bar() -> None:
    # Same on-thread sentence: admitted as a comment, rejected when tagged as a search doc.
    quote = "The flood at the camp left rescuers searching the river."
    ctx = PostContext.from_post(_POST)
    cfg = _default_cfg(min_relevance=0.4, search_relevance_mult=2.0)
    as_comment = build_tangent_db([_cand(quote, "Community Discussion")], ctx, cfg)
    as_search = build_tangent_db([_cand(quote, "Reference Material")], ctx, cfg)
    assert as_comment.report.kept_count == 1
    assert as_search.report.kept_count == 0


def test_near_duplicates_collapse_to_one() -> None:
    a = _cand("Rescue teams searched the river for the missing campers.", "Original Post")
    b = _cand("Rescue teams searched the river for the missing campers all night.", "Original Post")
    ctx = PostContext.from_post(_POST)
    cfg = _default_cfg(max_similarity=0.6)
    result = build_tangent_db([a, b], ctx, cfg)
    assert result.report.kept_count == 1
    assert result.report.dropped["near_duplicate"] == 1


def test_min_size_relaxes_similarity_never_relevance() -> None:
    a = _cand("Rescue teams searched the river for the missing campers.", "Original Post")
    b = _cand("Rescue teams searched the river for the missing campers all night.", "Original Post")
    ctx = PostContext.from_post(_POST)
    strict = _default_cfg(max_similarity=0.6, min_size=0)
    assert build_tangent_db([a, b], ctx, strict).report.kept_count == 1
    floored = _default_cfg(max_similarity=0.6, min_size=2)
    result = build_tangent_db([a, b], ctx, floored)
    assert result.report.kept_count == 2
    assert result.report.relaxations, "expected a logged similarity relaxation"
    # Relevance threshold is untouched by the floor.
    assert result.report.relevance["threshold"] == 0.12


def test_capacity_cap_drops_lowest_ranked() -> None:
    candidates = _relevant_candidates()
    ctx = PostContext.from_post(_POST)
    cfg = _default_cfg(max_output=1)
    result = build_tangent_db(candidates, ctx, cfg)
    assert result.report.kept_count == 1
    assert result.report.dropped["capped"] == 1


def test_config_hash_stable_and_sensitive() -> None:
    a = TangentDbConfig(min_relevance=0.12, max_similarity=0.7)
    b = TangentDbConfig(min_relevance=0.12, max_similarity=0.7)
    c = TangentDbConfig(min_relevance=0.2, max_similarity=0.7)
    assert a.config_hash() == b.config_hash()
    assert a.config_hash() != c.config_hash()


def test_empty_candidates_yield_empty_db() -> None:
    ctx = PostContext.from_post(_POST)
    result = build_tangent_db([], ctx, _default_cfg())
    assert result.angles == []
    assert result.report.kept_count == 0
    assert result.report.relevance == {
        "min": 0.0,
        "mean": 0.0,
        "median": 0.0,
        "max": 0.0,
        "threshold": 0.12,
        "scores_kept": [],
    }
