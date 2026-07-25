"""Provenance manifest for an isolated prepared-post corpus."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from infrastructure.config import (
    POSTS_DIRECTORY,
    get_step_dirs,
    get_workflow_angles_max_output,
    get_workflow_capacity_profile,
    get_workflow_dataset_root,
    get_workflow_tangent_db_builder,
    resolve_path,
)
from workflows.utils.tangent_db import tangent_db_config_from_env


class PrepRunManifest(BaseModel):
    """Self-describing recipe for one isolated preparation run."""

    schema_version: int = 1
    run_id: str
    tangent_db_builder: str
    tangent_db_config_hash: str
    capacity_profile: str
    created_at_utc: str
    seed_corpus: str
    dataset_root: str
    step_dirs: dict[str, dict[str, str]]
    notes: str = ""


def build_prep_run_manifest(*, run_id: str, notes: str = "") -> PrepRunManifest:
    """Build provenance from the currently effective workflow environment."""
    root = get_workflow_dataset_root()
    if root is None:
        raise ValueError("WORKFLOW_DATASET_ROOT is required for a prep-run manifest")
    step_dirs = {
        step: {"source": str(source), "dest": str(dest)}
        for step in (
            "filter-url-unresolved",
            "filter-researched",
            "angles-step",
            "final-step",
        )
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
