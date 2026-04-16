"""Pure helpers for comparing workflow JSON payloads (used by validate-post)."""

from __future__ import annotations

from typing import Any


def collect_diff_paths(
    left: Any,
    right: Any,
    prefix: str = "",
    limit: int = 50,
) -> list[str]:
    """Return up to ``limit`` JSON paths where ``left`` and ``right`` differ."""
    diffs: list[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if len(diffs) >= limit:
            return
        if type(a) is not type(b):
            diffs.append(path or "$")
            return
        if isinstance(a, dict):
            a_keys = set(a.keys())
            b_keys = set(b.keys())
            for key in sorted(a_keys - b_keys):
                if len(diffs) >= limit:
                    return
                next_path = f"{path}.{key}" if path else key
                diffs.append(next_path)
            for key in sorted(b_keys - a_keys):
                if len(diffs) >= limit:
                    return
                next_path = f"{path}.{key}" if path else key
                diffs.append(next_path)
            for key in sorted(a_keys & b_keys):
                next_path = f"{path}.{key}" if path else key
                walk(a[key], b[key], next_path)
            return
        if isinstance(a, list):
            if len(a) != len(b):
                diffs.append(path or "$")
                return
            for idx, (a_item, b_item) in enumerate(zip(a, b, strict=True)):
                next_path = f"{path}[{idx}]" if path else f"[{idx}]"
                walk(a_item, b_item, next_path)
            return
        if a != b:
            diffs.append(path or "$")

    walk(left, right, prefix)
    return diffs
