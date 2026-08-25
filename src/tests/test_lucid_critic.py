"""Unit tests for Project LUCID's structured TangentsDB critic."""

from workflows.utils.lucid_critic import (
    LucidCriticReplacement,
    LucidCriticRequest,
    LucidCriticResponse,
    apply_critic_proposal,
    critic_request_from_artifact,
)
from workflows.utils.lucid_tangent_db import LucidTangentCandidate, build_lucid_tangents_db

POST = "A city council delayed the river cleanup after flood damage."
PARENT = "The cleanup delay leaves nearby residents worried about the river."


def _candidate(tangent_id: str, subject: str, relation: str, cue: str) -> LucidTangentCandidate:
    return LucidTangentCandidate(
        tangent_id=tangent_id,
        subject=subject,
        relation=relation,
        thread_cue=cue,
        source_quote="The council discussed the river cleanup delay.",
    )


def test_apply_critic_proposal_accepts_separating_replacement() -> None:
    cleanup = _candidate("cleanup", "river cleanup", "was delayed", "residents worry")
    near_dup = _candidate("near", "river cleanup", "delayed again", "residents worry")
    flood = _candidate("flood", "flood damage", "caused the delay", "river concerns")
    baseline = build_lucid_tangents_db(
        post_id="p1",
        post_context=POST,
        parent_context=PARENT,
        candidates=[cleanup, near_dup, flood],
        size=2,
    )
    request = critic_request_from_artifact(
        baseline.artifact, post_context=POST, parent_context=PARENT, codebook_size=2
    )
    replacement = LucidCriticReplacement(
        drop_id="near",
        add=_candidate("council", "city council", "delayed cleanup", "nearby residents"),
    )
    response = LucidCriticResponse(replace=[replacement], notes=["separate near-duplicate"])
    applied = apply_critic_proposal(request=request, response=response)
    assert applied.accepted is True
    assert applied.result is not None
    assert "near" not in {item.tangent_id for item in applied.result.selected}


def test_apply_critic_proposal_rejects_empty_and_bad_drop() -> None:
    cleanup = _candidate("cleanup", "river cleanup", "was delayed", "residents worry")
    flood = _candidate("flood", "flood damage", "caused the delay", "river concerns")
    baseline = build_lucid_tangents_db(
        post_id="p1", post_context=POST, parent_context=PARENT, candidates=[cleanup, flood], size=2
    )
    request = LucidCriticRequest(
        artifact_hash=baseline.artifact.content_hash,
        post_id="p1",
        post_context=POST,
        parent_context=PARENT,
        parent_context_hash=baseline.artifact.parent_context_hash,
        candidates=[cleanup, flood],
        pairwise_separation=baseline.artifact.pairwise_separation,
        selected_tangent_ids=baseline.artifact.selected_tangent_ids,
        codebook_size=2,
    )
    empty = apply_critic_proposal(request=request, response=LucidCriticResponse(replace=[]))
    assert empty.accepted is False
    bad = apply_critic_proposal(
        request=request,
        response=LucidCriticResponse(
            replace=[
                LucidCriticReplacement(
                    drop_id="missing",
                    add=_candidate("x", "river cleanup", "was delayed", "residents worry"),
                )
            ]
        ),
    )
    assert bad.accepted is False
    assert bad.critic_rejected >= 1
