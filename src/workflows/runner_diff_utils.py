"""Pure helpers for comparing workflow JSON payloads (used by validate-post)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import validate_call


class _Absent:
    __slots__ = ()


_ABSENT = _Absent()


def _json_snippet(value: Any, cap: int) -> str:
    if value is _ABSENT:
        return "(absent)"
    if isinstance(value, str):
        return value if len(value) <= cap else f"{value[: cap - 3]}..."
    try:
        text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except TypeError:
        text = str(value)
    return text if len(text) <= cap else f"{text[: cap - 3]}..."


@validate_call
def collect_mismatch_value_snippets(
    left: Any,
    right: Any,
    *,
    path_limit: int = 8,
    snippet_len: int = 96,
) -> list[dict[str, str]]:
    """Return up to ``path_limit`` rows with JSON paths and capped baseline/rerun snippets."""
    pl = max(1, min(path_limit, 50))
    sl = max(16, min(snippet_len, 512))
    out: list[dict[str, str]] = []

    def append_row(path: str, av: Any, bv: Any) -> None:
        if len(out) >= pl:
            return
        out.append(
            {
                "path": path or "$",
                "baseline_snippet": _json_snippet(av, sl),
                "rerun_snippet": _json_snippet(bv, sl),
            }
        )

    def walk(a: Any, b: Any, path: str) -> None:
        if len(out) >= pl:
            return
        if type(a) is not type(b):
            append_row(path, a, b)
            return
        if isinstance(a, dict):
            _walk_dict_mismatch_snippets(a, b, path, append_row, walk)
            return
        if isinstance(a, list):
            if len(a) != len(b):
                append_row(path or "$", a, b)
                return
            for idx, (ai, bi) in enumerate(zip(a, b, strict=True)):
                p = f"{path}[{idx}]" if path else f"[{idx}]"
                walk(ai, bi, p)
            return
        if a != b:
            append_row(path, a, b)

    walk(left, right, "")
    return out


def _walk_dict_mismatch_snippets(
    a: dict[Any, Any],
    b: dict[Any, Any],
    path: str,
    append_row: Callable[[str, Any, Any], None],
    walk: Callable[[Any, Any, str], None],
) -> None:
    a_keys, b_keys = set(a.keys()), set(b.keys())
    for key in sorted(a_keys - b_keys):
        p = f"{path}.{key}" if path else str(key)
        append_row(p, a[key], _ABSENT)
    for key in sorted(b_keys - a_keys):
        p = f"{path}.{key}" if path else str(key)
        append_row(p, _ABSENT, b[key])
    for key in sorted(a_keys & b_keys):
        p = f"{path}.{key}" if path else str(key)
        walk(a[key], b[key], p)


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
