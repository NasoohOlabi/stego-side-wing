"""Shared experiment variant loading and environment application."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, Field, validate_call

from infrastructure.config import REPO_ROOT, WorkflowEncodingProfile

PROFILE_OVERRIDE_KEYS = (
    "WORKFLOW_ANGLES_GENERATION_MODE",
    "WORKFLOW_STEGO_GENERATION_MODE",
    "WORKFLOW_CAPACITY_PROFILE",
    "WORKFLOW_CAPACITY_LIMITS_ENABLED",
    "WORKFLOW_PAYLOAD_TRANSFORM",
    "WORKFLOW_STEGO_PROMPT_STYLE",
    "WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT",
    "WORKFLOW_STEGO_MAX_RETRIES",
    "WORKFLOW_DECODE_SEMANTIC_TOP_N",
    "WORKFLOW_DECODE_LLM_MAX_TRIES",
    "WORKFLOW_STEGO_LLM_TEMPERATURE",
    "WORKFLOW_DECODE_STRICT_DEFAULT",
    "WORKFLOW_NATURALNESS_GATE_ENABLED",
    "WORKFLOW_NATURALNESS_GATE_MODE",
    "WORKFLOW_BARB_STANCE_GATE",
)


class ExperimentVariant(BaseModel):
    """Named experiment lane with env overrides on top of a base profile."""

    name: str = Field(min_length=1)
    base_profile: WorkflowEncodingProfile
    description: str = ""
    env_overrides: dict[str, str] = Field(default_factory=dict)
    synthetic_force_model_generation: bool | None = None
    real_force_model_generation: bool | None = None


class ExperimentVariantManifest(BaseModel):
    """Variant manifest loaded from ``config/pareto_variants.json``."""

    version: int = Field(ge=1)
    variants: list[ExperimentVariant]


def experiment_variants_path() -> Path:
    return (REPO_ROOT / "config" / "pareto_variants.json").resolve()


def _default_profile_variant(name: str) -> ExperimentVariant | None:
    normalized = name.strip().lower()
    if normalized == "balanced_naturalness_gate":
        return ExperimentVariant(
            name=normalized,
            base_profile="balanced",
            description="Balanced profile with the middle-ground naturalness gate enabled.",
            env_overrides={
                "WORKFLOW_NATURALNESS_GATE_ENABLED": "1",
                "WORKFLOW_NATURALNESS_GATE_MODE": "middle",
            },
        )
    if normalized not in {"balanced", "robustness", "capacity", "security"}:
        return None
    profile = normalized  # pyright narrowing needs an explicit branch below.
    if profile == "balanced":
        return ExperimentVariant(
            name=profile, base_profile="balanced", description="ad hoc profile"
        )
    if profile == "robustness":
        return ExperimentVariant(
            name=profile, base_profile="robustness", description="ad hoc profile"
        )
    if profile == "capacity":
        return ExperimentVariant(
            name=profile, base_profile="capacity", description="ad hoc profile"
        )
    return ExperimentVariant(name=profile, base_profile="security", description="ad hoc profile")


@validate_call
def load_experiment_variant_manifest(
    path: Path | None = None,
) -> ExperimentVariantManifest:
    resolved = path or experiment_variants_path()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return ExperimentVariantManifest.model_validate(payload)


@validate_call
def load_experiment_variants(
    path: Path | None = None,
) -> dict[str, ExperimentVariant]:
    manifest = load_experiment_variant_manifest(path)
    return {variant.name: variant for variant in manifest.variants}


@validate_call
def resolve_experiment_variants(
    names: Sequence[str],
    path: Path | None = None,
) -> list[ExperimentVariant]:
    known = load_experiment_variants(path)
    resolved: list[ExperimentVariant] = []
    for raw_name in names:
        name = raw_name.strip()
        if not name:
            continue
        if name in known:
            resolved.append(known[name])
            continue
        fallback = _default_profile_variant(name)
        if fallback is not None:
            resolved.append(fallback)
            continue
        raise ValueError(f"Unknown experiment variant: {name}")
    return resolved


def _resolved_payload_transform(variant: ExperimentVariant) -> str:
    override = variant.env_overrides.get("WORKFLOW_PAYLOAD_TRANSFORM", "").strip().lower()
    if override:
        return override
    if variant.base_profile == "security":
        return "secure_compact_v2"
    return "plain"


def variant_uses_secret(variant: ExperimentVariant) -> bool:
    return _resolved_payload_transform(variant) in {"hmac_xor_v1", "secure_compact_v2"}


@contextmanager
def applied_experiment_variant(
    variant: ExperimentVariant,
    *,
    force_model_generation: bool,
    default_secret: str | None = None,
) -> Iterator[None]:
    keys = ("WORKFLOW_ENCODING_PROFILE", *PROFILE_OVERRIDE_KEYS, "WORKFLOW_ENCODING_SECRET")
    old_values = {key: os.environ.get(key) for key in keys}
    try:
        for key in PROFILE_OVERRIDE_KEYS:
            os.environ.pop(key, None)
        os.environ["WORKFLOW_ENCODING_PROFILE"] = variant.base_profile
        for key, value in variant.env_overrides.items():
            os.environ[key] = str(value)
        if force_model_generation:
            os.environ["WORKFLOW_ANGLES_GENERATION_MODE"] = "model"
            os.environ["WORKFLOW_STEGO_GENERATION_MODE"] = "model"
        if default_secret and variant_uses_secret(variant):
            os.environ.setdefault("WORKFLOW_ENCODING_SECRET", default_secret)
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
