"""Helpers for protocol-level reproducibility reporting."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def stable_json_dumps(value: Any) -> str:
    """Encode JSON-compatible data with stable ordering for hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_hash(value: Any) -> str:
    """Hash strings or JSON-compatible values deterministically."""
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = stable_json_dumps(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def text_preview(text: str | None, limit: int = 160) -> str:
    """Return a compact single-line preview for logs and APIs."""
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    """Deduplicate strings while preserving first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered
