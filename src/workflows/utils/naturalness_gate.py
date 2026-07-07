"""Heuristics for keeping stego samples context-faithful and comment-like."""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from pydantic import validate_call

_STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "with",
}

_CANNED_SUFFIXES = (
    "that seems directly relevant to what happened here",
    "that detail feels important in this situation",
)

_FINITE_VERBS = {
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "can",
    "could",
    "will",
    "would",
    "should",
    "might",
    "must",
    "seems",
    "feels",
    "looks",
    "shows",
    "means",
    "matters",
}


def naturalness_gate_enabled() -> bool:
    raw = os.environ.get("WORKFLOW_NATURALNESS_GATE_ENABLED", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def naturalness_gate_mode() -> str:
    raw = os.environ.get("WORKFLOW_NATURALNESS_GATE_MODE", "middle").strip().lower()
    if raw in {"strict", "middle", "report"}:
        return raw
    return "middle"


def tokenize_content_words(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9'-]+", text.lower())
        if len(token) >= 4 and token not in _STOPWORDS
    ]


def _context_texts_from_post(post: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for key in ("title", "selftext"):
        value = post.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    for comment in _flatten_comments(post.get("comments", [])):
        body = comment.get("body")
        if isinstance(body, str) and body.strip():
            texts.append(body)
    return texts


def _flatten_comments(comments: Any) -> list[dict[str, Any]]:
    if not isinstance(comments, list):
        return []
    out: list[dict[str, Any]] = []
    stack = list(reversed(comments))
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        out.append(item)
        replies = item.get("replies")
        if isinstance(replies, list):
            stack.extend(reversed(replies))
    return out


def _angle_text(angle: dict[str, Any]) -> str:
    parts = [str(angle.get(key) or "") for key in ("source_quote", "tangent", "category")]
    return " ".join(parts)


def _looks_like_fragment(text: str) -> bool:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return True
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", normalized)
    content_words = tokenize_content_words(normalized)
    if len(content_words) < 3:
        return True
    lowered_words = {word.lower() for word in words}
    has_verb = bool(lowered_words & _FINITE_VERBS) or any(
        word.lower().endswith(("ed", "ing")) for word in words
    )
    if len(words) <= 7 and not has_verb:
        return True
    if normalized.count("'") % 2 == 1 or normalized.count('"') % 2 == 1:
        return True
    return False


@validate_call
def score_angle_relevance(
    angle: dict[str, Any],
    post: dict[str, Any],
) -> dict[str, Any]:
    angle_tokens = tokenize_content_words(_angle_text(angle))
    context_tokens = tokenize_content_words(" ".join(_context_texts_from_post(post)))
    context_counts = Counter(context_tokens)
    overlap = sorted({token for token in angle_tokens if context_counts[token] > 0})
    source_quote = str(angle.get("source_quote") or "")
    reasons: list[str] = []
    fragment = _looks_like_fragment(source_quote)
    if _looks_like_fragment(source_quote):
        reasons.append("source_quote_fragment")
    if len(overlap) < 2:
        reasons.append("weak_post_relevance")
    score = len(overlap) / max(1, len(set(angle_tokens)))
    mode = naturalness_gate_mode()
    if mode == "report":
        blocking_reasons: list[str] = []
    elif mode == "strict":
        blocking_reasons = [reason for reason in reasons if reason != "source_quote_fragment"]
        if fragment and len(overlap) < 3:
            blocking_reasons.append("source_quote_fragment")
    else:
        blocking_reasons = ["weak_post_relevance"] if not overlap else []
    passes = not blocking_reasons
    return {
        "passes": passes,
        "score": score,
        "mode": mode,
        "overlap_tokens": overlap[:12],
        "angle_token_count": len(set(angle_tokens)),
        "context_token_count": len(set(context_tokens)),
        "reasons": reasons,
    }


@validate_call
def filter_angles_for_post(
    angles: list[dict[str, Any]],
    post: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, angle in enumerate(angles):
        score = score_angle_relevance(angle, post)
        if score["passes"]:
            kept.append(angle)
            continue
        rejected.append(
            {
                "index": index,
                "score": score["score"],
                "reasons": score["reasons"],
                "source_quote": angle.get("source_quote"),
                "category": angle.get("category"),
            }
        )
    reason_counts = Counter(reason for item in rejected for reason in item.get("reasons", []))
    report = {
        "enabled": True,
        "input_count": len(angles),
        "kept_count": len(kept),
        "rejected_count": len(rejected),
        "reason_counts": dict(reason_counts),
        "rejected_sample": rejected[:20],
    }
    return kept, report


@validate_call
def comment_plausibility_gate(
    text: str,
) -> dict[str, Any]:
    normalized = " ".join(text.split()).strip()
    lowered = normalized.lower().rstrip(".!?:; ")
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", normalized)
    content_words = tokenize_content_words(normalized)
    reasons: list[str] = []
    if not normalized:
        reasons.append("empty_comment")
    if any(lowered.endswith(suffix) for suffix in _CANNED_SUFFIXES):
        reasons.append("canned_generic_suffix")
    if _looks_like_fragment(normalized):
        reasons.append("title_like_fragment")
    if len(words) < 5:
        reasons.append("too_short")
    if normalized.count("'") % 2 == 1 or normalized.count('"') % 2 == 1:
        reasons.append("broken_quote")
    if len(content_words) < 3:
        reasons.append("low_content")
    return {
        "passes": not reasons,
        "word_count": len(words),
        "content_word_count": len(content_words),
        "reasons": reasons,
    }
