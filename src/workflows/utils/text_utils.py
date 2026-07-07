"""Shared text helpers used across workflow pipelines."""

from __future__ import annotations

import json
import math
from typing import Any

from infrastructure.config import (
    get_workflow_angles_max_input_blocks,
    get_workflow_capacity_profile,
    get_workflow_dictionary_max_comments,
    get_workflow_dictionary_max_search_results,
)
from workflows.utils.protocol_utils import stable_hash, text_preview


def chunk_text_equal_overlap(
    text: str,
    num_chunks: int,
    overlap_chars: int,
) -> list[str]:
    """
    Split `text` into `num_chunks` windows of equal nominal width with a fixed
    character overlap between consecutive windows. Every character of `text`
    appears in at least one chunk; no content is trimmed or dropped.

    Window width W is chosen so that (n-1)*(W - overlap) + W >= len(text), i.e.
    n*W - (n-1)*overlap >= L, using W = ceil((L + (n-1)*overlap) / n), capped by L.

    Args:
        text: Full string to partition (empty -> []).
        num_chunks: Number of overlapping parts (>= 1).
        overlap_chars: Non-negative overlap between consecutive chunks.

    Raises:
        ValueError: If num_chunks < 1 or overlap_chars < 0.
    """
    if num_chunks < 1:
        raise ValueError("num_chunks must be >= 1")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must be non-negative")
    if not text:
        return []
    if num_chunks == 1:
        return [text]

    L = len(text)
    n = num_chunks
    overlap = overlap_chars

    numer = L + (n - 1) * overlap
    win = max(1, math.ceil(numer / n))
    win = min(win, L)
    stride = win - overlap
    if stride < 1:
        stride = 1
        win = min(L, stride + overlap)

    chunks: list[str] = []
    for i in range(n):
        start = i * stride
        if start >= L:
            break
        end = min(L, start + win)
        chunks.append(text[start:end])
        if end >= L:
            break

    return chunks if chunks else [text]


def flatten_comments(comments: Any) -> list[dict[str, Any]]:
    """Flatten nested comment trees into a simple list."""
    if not isinstance(comments, list):
        return []
    flattened: list[dict[str, Any]] = []

    def walk(comment: Any) -> None:
        if not isinstance(comment, dict):
            return
        flattened.append(comment)
        replies = comment.get("replies", [])
        if isinstance(replies, list):
            for reply in replies:
                walk(reply)

    for top_level in comments:
        walk(top_level)
    return flattened


def _search_result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        text = result.get("text") or result.get("snippet", "")
        return text if isinstance(text, str) else ""
    return ""


def _dictionary_entry(
    source: str,
    source_index: int,
    text: str,
    *,
    comment_id: str | None = None,
) -> dict[str, Any]:
    entry = {"source": source, "source_index": source_index, "text": text}
    if comment_id is not None:
        entry["comment_id"] = comment_id
    return entry


def build_post_text_dictionary_entries(post: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect ordered text entries from post body, search results, and comments."""
    entries: list[dict[str, Any]] = []
    selftext = post.get("selftext") or post.get("text", "")
    if isinstance(selftext, str) and selftext:
        entries.append(_dictionary_entry("post", 0, selftext))

    search_results = post.get("search_results", [])
    if isinstance(search_results, list):
        for idx, result in enumerate(search_results):
            text = _search_result_text(result)
            if text:
                entries.append(_dictionary_entry("search_results", idx, text))

    for idx, comment in enumerate(flatten_comments(post.get("comments", []))):
        body = comment.get("body", "")
        if isinstance(body, str) and body:
            raw_comment_id = comment.get("id")
            comment_id = (
                str(raw_comment_id)
                if raw_comment_id is not None and str(raw_comment_id).strip()
                else None
            )
            entries.append(
                _dictionary_entry(
                    "comments",
                    idx,
                    body,
                    comment_id=comment_id,
                )
            )
    return entries


def _limit_source_entries(
    entries: list[dict[str, Any]], source: str, keep: int
) -> tuple[list[dict[str, Any]], bool]:
    kept: list[dict[str, Any]] = []
    source_seen = 0
    truncated = False
    for entry in entries:
        if entry["source"] != source:
            kept.append(entry)
            continue
        if source_seen < keep:
            kept.append(entry)
            source_seen += 1
            continue
        truncated = True
    return kept, truncated


def _source_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"post": 0, "search_results": 0, "comments": 0}
    for entry in entries:
        source = str(entry.get("source", ""))
        if source in counts:
            counts[source] += 1
    return counts


def _entry_meta(entry: dict[str, Any]) -> dict[str, Any]:
    text = str(entry.get("text", ""))
    return {
        "source": str(entry.get("source", "")),
        "source_index": int(entry.get("source_index", 0)),
        "comment_id": entry.get("comment_id"),
        "text_hash": stable_hash(text),
        "text_length": len(text),
        "preview": text_preview(text, limit=120),
    }


def apply_post_text_dictionary_capacity(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply global workflow capacity limits while preserving stable order."""
    capped, search_capped = _limit_source_entries(
        entries, "search_results", get_workflow_dictionary_max_search_results()
    )
    capped, comments_capped = _limit_source_entries(
        capped, "comments", get_workflow_dictionary_max_comments()
    )
    max_blocks = get_workflow_angles_max_input_blocks()
    total_capped = len(capped) > max_blocks
    if total_capped:
        capped = capped[:max_blocks]
    truncated_sources = [
        source
        for source, capped_flag in (
            ("search_results", search_capped),
            ("comments", comments_capped),
            ("total", total_capped),
        )
        if capped_flag
    ]
    return capped, {
        "capacity_profile": get_workflow_capacity_profile(),
        "capacity_limits": {
            "dictionary_max_search_results": get_workflow_dictionary_max_search_results(),
            "dictionary_max_comments": get_workflow_dictionary_max_comments(),
            "angles_max_input_blocks": max_blocks,
        },
        "capacity_applied": bool(truncated_sources),
        "truncated_sources": truncated_sources,
    }


def _dictionary_report(
    raw_entries: list[dict[str, Any]],
    final_entries: list[dict[str, Any]],
    capacity_meta: dict[str, Any],
) -> dict[str, Any]:
    final_meta = [_entry_meta(entry) for entry in final_entries]
    final_texts = [str(entry.get("text", "")) for entry in final_entries]
    return {
        "dictionary_id": stable_hash(final_meta),
        "texts_hash": stable_hash(final_texts),
        "raw_entry_count": len(raw_entries),
        "entry_count": len(final_entries),
        "raw_source_counts": _source_counts(raw_entries),
        "source_counts": _source_counts(final_entries),
        "sample_entries": final_meta[:5],
        **capacity_meta,
    }


def build_post_text_dictionary_bundle(
    post: dict[str, Any], *, apply_capacity_profile: bool = False
) -> dict[str, Any]:
    """Return ordered dictionary texts plus a deterministic, source-aware report."""
    raw_entries = build_post_text_dictionary_entries(post)
    capacity_meta = {
        "capacity_profile": None,
        "capacity_limits": {},
        "capacity_applied": False,
        "truncated_sources": [],
    }
    final_entries = raw_entries
    if apply_capacity_profile:
        final_entries, capacity_meta = apply_post_text_dictionary_capacity(raw_entries)
    return {
        "entries": [dict(entry) for entry in final_entries],
        "texts": [str(entry.get("text", "")) for entry in final_entries],
        "report": _dictionary_report(raw_entries, final_entries, capacity_meta),
    }


def build_post_text_dictionary(
    post: dict[str, Any], *, apply_capacity_profile: bool = False
) -> list[str]:
    """Collect searchable text chunks from post body, search results, and comments."""
    return list(
        build_post_text_dictionary_bundle(post, apply_capacity_profile=apply_capacity_profile)[
            "texts"
        ]
    )


def build_post_text_dictionary_report(
    post: dict[str, Any], *, apply_capacity_profile: bool = False
) -> dict[str, Any]:
    """Deterministic metadata for dictionary drift and observability checks."""
    return dict(
        build_post_text_dictionary_bundle(post, apply_capacity_profile=apply_capacity_profile)[
            "report"
        ]
    )


def parse_json_array_response(response: str) -> list[Any]:
    """Parse list-like LLM output with markdown/extra text tolerance."""
    candidate = response.strip()
    if candidate.startswith("```"):
        lines = candidate.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    start_idx = candidate.find("[")
    end_idx = candidate.rfind("]")
    if start_idx >= 0 and end_idx > start_idx:
        try:
            parsed = json.loads(candidate[start_idx : end_idx + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []
