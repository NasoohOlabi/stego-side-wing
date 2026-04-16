"""Shared text helpers used across workflow pipelines."""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List


def chunk_text_equal_overlap(
    text: str,
    num_chunks: int,
    overlap_chars: int,
) -> List[str]:
    """
    Split `text` into `num_chunks` windows of equal nominal width with a fixed
    character overlap between consecutive windows. Every character of `text`
    appears in at least one chunk; no content is trimmed or dropped.

    Window width W is chosen so that (n-1)*(W - O) + W >= len(text), i.e.
    n*W - (n-1)*O >= L, using W = ceil((L + (n-1)*O) / n), capped by L.

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
    O = overlap_chars

    numer = L + (n - 1) * O
    win = max(1, math.ceil(numer / n))
    win = min(win, L)
    stride = win - O
    if stride < 1:
        stride = 1
        win = min(L, stride + O)

    chunks: List[str] = []
    for i in range(n):
        start = i * stride
        if start >= L:
            break
        end = min(L, start + win)
        chunks.append(text[start:end])
        if end >= L:
            break

    return chunks if chunks else [text]


def flatten_comments(comments: Any) -> List[Dict[str, Any]]:
    """Flatten nested comment trees into a simple list."""
    if not isinstance(comments, list):
        return []
    flattened: List[Dict[str, Any]] = []

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


def build_post_text_dictionary(post: Dict[str, Any]) -> List[str]:
    """Collect searchable text chunks from post body, search results, and comments."""
    dictionary: List[str] = []
    selftext = post.get("selftext") or post.get("text", "")
    if isinstance(selftext, str) and selftext:
        dictionary.append(selftext)

    search_results = post.get("search_results", [])
    if isinstance(search_results, list):
        for result in search_results:
            if isinstance(result, str) and result:
                dictionary.append(result)
            elif isinstance(result, dict):
                text = result.get("text") or result.get("snippet", "")
                if isinstance(text, str) and text:
                    dictionary.append(text)

    for comment in flatten_comments(post.get("comments", [])):
        body = comment.get("body", "")
        if isinstance(body, str) and body:
            dictionary.append(body)

    return dictionary


def parse_json_array_response(response: str) -> List[Any]:
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
