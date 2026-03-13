"""Shared text helpers used across workflow pipelines."""
from __future__ import annotations

import json
from typing import Any, Dict, List


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
