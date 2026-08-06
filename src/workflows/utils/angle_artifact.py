"""Shared identity contracts for versioned angle-generation artifacts."""

from typing import Any

from pydantic import BaseModel, Field

ANGLE_ARTIFACT_SCHEMA_VERSION = 2
ANGLE_ARTIFACT_NAMESPACE = "selection_channel_angles/refactor_v2"
ANGLE_GENERATOR_VERSION = "efficient_multiframe_selection_v1"
PREP_MANIFEST_SCHEMA_VERSION = 2
CONTEXT_ANGLE_ARTIFACT_NAMESPACE = "selection_channel_angles/context_weighted_v2"


class ParentConditionedAngleArtifact(BaseModel):
    """Validated identity for one parent-conditioned tangent codebook."""

    schema_version: int = 3
    artifact_namespace: str = CONTEXT_ANGLE_ARTIFACT_NAMESPACE
    generator_version: str = ANGLE_GENERATOR_VERSION
    sampler_version: str = "context_weighted_v2"
    post_id: str
    selected_parent_id: str | None = None
    dictionary_id: str
    frozen_research_hash: str
    relationship_counts: dict[str, int] = Field(default_factory=dict)
    requested_allocations: dict[str, int] = Field(default_factory=dict)
    effective_allocations: dict[str, int] = Field(default_factory=dict)
    capacity_limits: dict[str, Any] = Field(default_factory=dict)
    tangent_hash: str
    tangent_count: int = Field(ge=0)
    parent_recoverable_width: int = Field(default=0, ge=0)
    tangent_recoverable_width: int = Field(default=0, ge=0)


def assert_single_sampler_lane(posts: list[dict[str, Any]]) -> str | None:
    """Reject explicit post-level/v2 artifact mixing in one generation lane."""
    versions = {
        str(artifact["sampler_version"])
        for post in posts
        if isinstance((artifact := post.get("angle_artifact")), dict)
        and artifact.get("sampler_version")
    }
    if len(versions) > 1:
        raise ValueError(f"Mixed angle sampler versions are not allowed: {sorted(versions)}")
    return next(iter(versions), None)
