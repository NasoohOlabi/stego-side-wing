"""Unit tests for Project LUCID's deterministic TangentsDB foundation."""

from workflows.utils.lucid_tangent_db import (
    LucidTangentCandidate,
    build_lucid_tangents_db,
)

POST = "A city council delayed the river cleanup after flood damage."
PARENT = "The cleanup delay leaves nearby residents worried about the river."


def _candidate(tangent_id: str, subject: str, relation: str, cue: str) -> LucidTangentCandidate:
    return LucidTangentCandidate(
        tangent_id=tangent_id, subject=subject, relation=relation, thread_cue=cue,
        source_quote="The council discussed the river cleanup delay.",
    )


def test_lucid_rejects_generic_and_parent_ungrounded_intents() -> None:
    valid = _candidate("valid", "river cleanup", "was delayed", "nearby residents worry")
    generic = _candidate("generic", "topic", "discussion", "issue")
    result = build_lucid_tangents_db(
        post_id="p1", post_context=POST, parent_context=PARENT,
        candidates=[valid, generic], size=2,
    )
    assert result.artifact.selected_tangent_ids == ["valid"]
    assert result.artifact.candidate_scores[1].rejection_reasons == [
        "not_post_grounded", "not_parent_grounded", "generic_intent"
    ]


def test_lucid_prefers_semantically_separated_codebook_and_freezes_hash() -> None:
    cleanup = _candidate("cleanup", "river cleanup", "was delayed", "residents worry")
    duplicate = _candidate("duplicate", "river cleanup", "was delayed", "nearby residents worry")
    flood = _candidate("flood", "flood damage", "caused the delay", "river concerns")
    first = build_lucid_tangents_db(
        post_id="p1", post_context=POST, parent_context=PARENT,
        candidates=[cleanup, duplicate, flood], size=2,
    )
    second = build_lucid_tangents_db(
        post_id="p1", post_context=POST, parent_context=PARENT,
        candidates=[cleanup, duplicate, flood], size=2,
    )
    assert first.artifact.content_hash == second.artifact.content_hash
    assert first.artifact.selected_tangent_ids == ["cleanup", "flood"]
    assert first.artifact.pairwise_separation["minimum"] > 0.5


def test_lucid_retains_candidate_scores_for_auditable_rejections() -> None:
    no_parent_cue = _candidate("bad", "river cleanup", "was delayed", "unrelated school")
    result = build_lucid_tangents_db(
        post_id="p1", post_context=POST, parent_context=PARENT,
        candidates=[no_parent_cue], size=1,
    )
    score = result.artifact.candidate_scores[0]
    assert not score.accepted
    assert "not_parent_grounded" in score.rejection_reasons
    assert result.selected == []
