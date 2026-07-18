"""Git provenance attached to generated final samples."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from infrastructure.config import REPO_ROOT


class SampleProvenance(BaseModel):
    """Reproducibility metadata for one generated sample."""

    schemaVersion: int = 1
    generatedAtUtc: str
    gitCommit: str | None = None
    gitBranch: str | None = None
    gitDirty: bool | None = None


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def build_sample_provenance(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    """Return best-effort Git metadata without blocking sample generation."""
    status = _git(repo_root, "status", "--porcelain")
    metadata = SampleProvenance(
        generatedAtUtc=datetime.now(UTC).isoformat(),
        gitCommit=_git(repo_root, "rev-parse", "HEAD"),
        gitBranch=_git(repo_root, "branch", "--show-current"),
        gitDirty=None if status is None else bool(status),
    )
    return metadata.model_dump()


def attach_sample_provenance(data: object) -> object:
    """Attach metadata to a final n8n artifact while preserving its root shape."""
    metadata = build_sample_provenance()
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        item = dict(data[0])
        embedding = item.get("embedding")
        item["embedding"] = {
            **(embedding if isinstance(embedding, dict) else {}),
            "generationMeta": metadata,
        }
        return [item]
    if isinstance(data, dict):
        embedding = data.get("embedding")
        return {
            **data,
            "embedding": {
                **(embedding if isinstance(embedding, dict) else {}),
                "generationMeta": metadata,
            },
        }
    return data
