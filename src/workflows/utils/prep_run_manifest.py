"""Provenance manifest for an isolated prepared-post corpus."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from infrastructure.config import (
    POSTS_DIRECTORY,
    get_step_dirs,
    get_workflow_angles_generation_mode,
    get_workflow_angles_max_output,
    get_workflow_capacity_profile,
    get_workflow_capacity_settings,
    get_workflow_dataset_root,
    get_workflow_tangent_db_builder,
    resolve_path,
)
from workflows.stages import PREP_RUN_STEPS
from workflows.utils.angle_artifact import (
    ANGLE_ARTIFACT_NAMESPACE,
    ANGLE_GENERATOR_VERSION,
    PREP_MANIFEST_SCHEMA_VERSION,
)
from workflows.utils.tangent_db import tangent_db_config_from_env
from workflows.utils.text_utils import DICTIONARY_SAMPLER_VERSION


class PrepRunManifest(BaseModel):
    """Self-describing recipe for one isolated preparation run."""

    schema_version: int = PREP_MANIFEST_SCHEMA_VERSION
    run_id: str
    tangent_db_builder: str
    tangent_db_config_hash: str
    capacity_profile: str
    created_at_utc: str
    seed_corpus: str
    dataset_root: str
    step_dirs: dict[str, dict[str, str]]
    artifact_namespace: str = "legacy_unversioned"
    angle_generator_version: str = "legacy_unversioned"
    angle_sampler_version: str = "legacy_unversioned"
    angle_generation_mode: str = "unknown"
    capacity_settings: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


def build_prep_run_manifest(*, run_id: str, notes: str = "") -> PrepRunManifest:
    """Build provenance from the currently effective workflow environment."""
    root = get_workflow_dataset_root()
    if root is None:
        raise ValueError("WORKFLOW_DATASET_ROOT is required for a prep-run manifest")
    step_dirs = {
        step: {"source": str(source), "dest": str(dest)}
        for step in PREP_RUN_STEPS
        for source, dest in [get_step_dirs(step)]
    }
    tangent_cfg = tangent_db_config_from_env(get_workflow_angles_max_output())
    return PrepRunManifest(
        run_id=run_id,
        tangent_db_builder=get_workflow_tangent_db_builder(),
        tangent_db_config_hash=tangent_cfg.config_hash(),
        capacity_profile=get_workflow_capacity_profile(),
        created_at_utc=datetime.now(UTC).isoformat(),
        seed_corpus=str(resolve_path(POSTS_DIRECTORY)),
        dataset_root=str(root),
        step_dirs=step_dirs,
        artifact_namespace=ANGLE_ARTIFACT_NAMESPACE,
        angle_generator_version=ANGLE_GENERATOR_VERSION,
        angle_sampler_version=DICTIONARY_SAMPLER_VERSION,
        angle_generation_mode=get_workflow_angles_generation_mode(),
        capacity_settings=get_workflow_capacity_settings(),
        notes=notes,
    )


def write_prep_run_manifest(*, run_id: str, notes: str = "") -> Path:
    """Write ``prep_run.json`` beneath the effective isolated dataset root."""
    manifest = build_prep_run_manifest(run_id=run_id, notes=notes)
    root = Path(manifest.dataset_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "prep_run.json"
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
