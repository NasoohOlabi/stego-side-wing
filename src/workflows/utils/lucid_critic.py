"""Project LUCID structured TangentsDB critic (pre-generation only).

The critic may propose replacements for overlapping codebook entries. Every
proposal is re-scored by ``build_lucid_tangents_db``; rejected proposals never
touch an already-generated carrier.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, validate_call

from workflows.utils.lucid_tangent_db import (
    LucidSelectionResult,
    LucidTangentCandidate,
    LucidTangentDbArtifact,
    build_lucid_tangents_db,
)
from workflows.utils.protocol_utils import stable_hash

LUCID_CRITIC_SCHEMA_VERSION = 1


class LucidCriticRequest(BaseModel):
    """Inputs the structured critic may see (no payload bits, no carrier text)."""

    schema_version: int = LUCID_CRITIC_SCHEMA_VERSION
    artifact_hash: str
    post_id: str
    post_context: str
    parent_context: str
    parent_context_hash: str
    candidates: list[LucidTangentCandidate]
    pairwise_separation: dict[str, float] = Field(default_factory=dict)
    selected_tangent_ids: list[str] = Field(default_factory=list)
    codebook_size: int = Field(ge=1)


class LucidCriticReplacement(BaseModel):
    """One drop/add proposal from the critic."""

    drop_id: str
    add: LucidTangentCandidate


class LucidCriticResponse(BaseModel):
    """Structured critic output; independently re-validated before use."""

    schema_version: int = LUCID_CRITIC_SCHEMA_VERSION
    replace: list[LucidCriticReplacement] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    model_identity: str = ""
    prompt_hash: str = ""


class LucidCriticApplication(BaseModel):
    """Result of applying a critic response under deterministic admission."""

    accepted: bool
    critic_rejected: int = 0
    applied_replacements: int = 0
    result: LucidSelectionResult | None = None
    rejection_notes: list[str] = Field(default_factory=list)


def critic_request_from_artifact(
    artifact: LucidTangentDbArtifact,
    *,
    post_context: str,
    parent_context: str,
    codebook_size: int,
) -> LucidCriticRequest:
    return LucidCriticRequest(
        artifact_hash=artifact.content_hash,
        post_id=artifact.post_id,
        post_context=post_context,
        parent_context=parent_context,
        parent_context_hash=artifact.parent_context_hash,
        candidates=list(artifact.candidates),
        pairwise_separation=dict(artifact.pairwise_separation),
        selected_tangent_ids=list(artifact.selected_tangent_ids),
        codebook_size=codebook_size,
    )


def _apply_replacements(
    candidates: list[LucidTangentCandidate], replacements: list[LucidCriticReplacement]
) -> tuple[list[LucidTangentCandidate], int, list[str]]:
    by_id = {item.tangent_id: item for item in candidates}
    rejected = 0
    notes: list[str] = []
    applied = 0
    for item in replacements:
        if item.drop_id not in by_id:
            rejected += 1
            notes.append(f"missing_drop_id:{item.drop_id}")
            continue
        if item.add.tangent_id in by_id and item.add.tangent_id != item.drop_id:
            rejected += 1
            notes.append(f"duplicate_add_id:{item.add.tangent_id}")
            continue
        del by_id[item.drop_id]
        by_id[item.add.tangent_id] = item.add
        applied += 1
    ordered = sorted(by_id.values(), key=lambda row: row.tangent_id)
    return ordered, rejected, notes


@validate_call
def apply_critic_proposal(
    *,
    request: LucidCriticRequest,
    response: LucidCriticResponse,
) -> LucidCriticApplication:
    """Re-score critic replacements; discard the whole proposal if admission shrinks."""
    if response.schema_version != LUCID_CRITIC_SCHEMA_VERSION:
        return LucidCriticApplication(
            accepted=False,
            critic_rejected=len(response.replace),
            rejection_notes=["unsupported_critic_schema"],
        )
    if not response.replace:
        return LucidCriticApplication(accepted=False, rejection_notes=["empty_replace_list"])

    pooled, rejected, notes = _apply_replacements(request.candidates, response.replace)
    applied_count = len(response.replace) - rejected
    if applied_count <= 0:
        return LucidCriticApplication(
            accepted=False,
            critic_rejected=rejected,
            applied_replacements=0,
            rejection_notes=[*notes, "no_replacements_applied"],
        )
    rebuilt = build_lucid_tangents_db(
        post_id=request.post_id,
        post_context=request.post_context,
        parent_context=request.parent_context,
        candidates=pooled,
        size=request.codebook_size,
    )
    min_needed = min(request.codebook_size, len(request.selected_tangent_ids) or request.codebook_size)
    if len(rebuilt.selected) < min_needed:
        return LucidCriticApplication(
            accepted=False,
            critic_rejected=rejected + applied_count,
            applied_replacements=0,
            rejection_notes=[*notes, "admission_shrunk_codebook"],
        )
    if rebuilt.artifact.pairwise_separation.get("minimum", 0.0) < request.pairwise_separation.get(
        "minimum", 0.0
    ):
        return LucidCriticApplication(
            accepted=False,
            critic_rejected=rejected + applied_count,
            rejection_notes=[*notes, "pairwise_separation_regressed"],
        )
    return LucidCriticApplication(
        accepted=True,
        critic_rejected=rejected,
        applied_replacements=applied_count,
        result=rebuilt,
        rejection_notes=notes,
    )


def parse_critic_response_payload(payload: dict[str, Any], *, prompt_hash: str = "", model_identity: str = "") -> LucidCriticResponse:
    """Validate a raw LLM JSON object into a critic response."""
    return LucidCriticResponse.model_validate(
        {
            **payload,
            "prompt_hash": prompt_hash or payload.get("prompt_hash") or "",
            "model_identity": model_identity or payload.get("model_identity") or "",
        }
    )


def hash_critic_prompt(system_template: str, user_template: str) -> str:
    return stable_hash({"system": system_template, "user": user_template})
