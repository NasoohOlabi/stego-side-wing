"""Helpers for protocol-level reproducibility reporting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any


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


def angle_signature(angle: dict[str, Any]) -> tuple[str, str, str]:
    """Identity of an angle for sender/receiver comparison.

    Sender and receiver must agree on which angle a decode refers to, so both sides
    compare this tuple rather than dict equality (angles pick up extra keys such as
    ``idx`` along the way).
    """
    return (
        str(angle.get("category", "")),
        str(angle.get("source_quote", "")),
        str(angle.get("tangent", "")),
    )


def text_preview(text: str | None, limit: int = 160) -> str:
    """Return a compact single-line preview for logs and APIs.

    Note this is *not* interchangeable with ``stego._text_preview``: that one defaults to
    180 chars and appends "..." after the cut (so the result can exceed the limit), while
    this one reserves room for the ellipsis inside ``limit``. Both feed different output
    fields, so they are deliberately kept separate.
    """
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
