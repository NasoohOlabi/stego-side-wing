"""Classify and normalize stego artifacts under ``output-results`` (n8n array shape)."""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal

MigrateOutcome = Literal["ok", "would_migrate", "migrated", "other", "error"]

N8N_ARTIFACT_KEYS = frozenset({"stegoText", "embedding", "post"})


class OutputResultsShapeKind(str, Enum):
    """Root JSON classification for migration."""

    OK = "ok"
    MIGRATABLE = "migratable"
    OTHER = "other"


def classify_output_results_root(data: Any) -> OutputResultsShapeKind:
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        if frozenset(data[0].keys()) == N8N_ARTIFACT_KEYS:
            return OutputResultsShapeKind.OK
    if isinstance(data, dict) and "stego_text" in data:
        return OutputResultsShapeKind.MIGRATABLE
    return OutputResultsShapeKind.OTHER


def n8n_save_object_body(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Same shape as n8n Save node: one-element array, camelCase keys."""
    stego = result.get("stego_text")
    stego_str = stego if isinstance(stego, str) else ""
    return [
        {
            "stegoText": stego_str,
            "embedding": result.get("embedding"),
            "post": result.get("post"),
        }
    ]


def assert_valid_n8n_stego_artifact(data: Any) -> None:
    """Raise when output artifact is not a strict n8n-compatible stego payload."""
    if not (isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict)):
        raise ValueError("Artifact must be a one-item list containing an object.")
    item = data[0]
    if frozenset(item.keys()) != N8N_ARTIFACT_KEYS:
        raise ValueError("Artifact object keys must be exactly stegoText, embedding, post.")
    stego_text = item.get("stegoText")
    if not (isinstance(stego_text, str) and stego_text.strip()):
        raise ValueError("Artifact stegoText must be a non-empty string.")


def migrate_output_results_file(path: Path, *, apply: bool) -> MigrateOutcome:
    """Load JSON at ``path``; if flat pipeline shape, rewrite to n8n array when ``apply``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "error"

    kind = classify_output_results_root(data)
    if kind is OutputResultsShapeKind.OK:
        return "ok"
    if kind is OutputResultsShapeKind.MIGRATABLE:
        assert isinstance(data, dict)
        out = n8n_save_object_body(data)
        if apply:
            with path.open("w", encoding="utf-8") as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
            return "migrated"
        return "would_migrate"
    return "other"
