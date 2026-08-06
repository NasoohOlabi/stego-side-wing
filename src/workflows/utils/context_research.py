"""Pure lexical relevance ranking over a frozen research pool."""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, validate_call

from workflows.utils.comment_context import normalize_visible_text
from workflows.utils.protocol_utils import stable_hash

_TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)


class RankedResearch(BaseModel):
    """Immutable research candidate and its reproducible relevance evidence."""

    model_config = ConfigDict(frozen=True)
    source_id: str
    url: str | None = None
    text: str
    text_hash: str
    score: float
    tie_breaker: str


def _terms(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN.finditer(text)}


def _research_fields(item: Any, index: int) -> tuple[str, str | None, str]:
    if isinstance(item, str):
        return f"research-{index}", None, normalize_visible_text(item)
    if not isinstance(item, dict):
        return f"research-{index}", None, ""
    url = item.get("url") or item.get("link")
    source_id = item.get("id") or url or f"research-{index}"
    text = item.get("text") or item.get("snippet") or ""
    return str(source_id), str(url) if url else None, normalize_visible_text(text)


@validate_call
def rank_frozen_research(
    research_pool: list[Any], query_document: str
) -> list[RankedResearch]:
    """Rank persisted snippets using normalized overlap and a SHA-256 final tie."""
    query_terms = _terms(normalize_visible_text(query_document))
    ranked: list[RankedResearch] = []
    for index, item in enumerate(research_pool):
        source_id, url, text = _research_fields(item, index)
        if not text:
            continue
        terms = _terms(text)
        overlap = len(query_terms & terms)
        score = overlap / math.sqrt(max(1, len(terms)))
        tie = stable_hash({"source_id": source_id, "url": url, "text": text})
        ranked.append(
            RankedResearch(
                source_id=source_id,
                url=url,
                text=text,
                text_hash=stable_hash(text),
                score=score,
                tie_breaker=tie,
            )
        )
    return sorted(ranked, key=lambda item: (-item.score, item.tie_breaker))
