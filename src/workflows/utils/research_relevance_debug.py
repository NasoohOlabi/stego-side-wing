"""Optional JSONL debug logs for research term and SERP relevance heuristics."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

_RE_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_DEBUG_COMPONENT = "ResearchRelevanceDebug"
_MAX_SNIPPET_CHARS = 400


def research_debug_log_dir() -> Optional[Path]:
    raw = (os.environ.get("RESEARCH_DEBUG_LOG_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _iso_utc_z() -> str:
    return (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _normalize_lower(s: str) -> str:
    return s.lower().strip()


def tokenize(text: str) -> frozenset[str]:
    return frozenset(_RE_TOKEN.findall(_normalize_lower(text)))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _base_record(*, trace_id: str, message: str) -> dict[str, Any]:
    return {
        "timestamp": _iso_utc_z(),
        "level": "DEBUG",
        "component": _DEBUG_COMPONENT,
        "trace_id": trace_id,
        "message": message,
    }


def term_overlap_metrics(
    term: str,
    *,
    corpus_tokens: frozenset[str],
    title_tokens: frozenset[str],
) -> dict[str, Any]:
    tt = tokenize(term)
    return {
        "term_char_len": len(term),
        "term_token_count": len(tt),
        "jaccard_vs_post_corpus": round(jaccard(tt, corpus_tokens), 4),
        "jaccard_vs_title_only": round(jaccard(tt, title_tokens), 4),
    }


def _domain_from_url(url: str) -> str:
    host = urlparse(url).netloc or ""
    return host.lower().removeprefix("www.")


def _snippet_preview(snippet: str) -> str:
    if len(snippet) <= _MAX_SNIPPET_CHARS:
        return snippet
    return snippet[: _MAX_SNIPPET_CHARS - 3] + "..."


def _hit_row_if_selected(
    rank: int,
    term: str,
    result: Dict[str, Any],
    seen_links: set[str],
    corpus_tokens: frozenset[str],
) -> Optional[Dict[str, Any]]:
    link = result.get("link", "") or ""
    if not link or link.endswith(".pdf") or link in seen_links:
        return None
    seen_links.add(link)
    title = str(result.get("title", "") or "")
    snippet = str(result.get("snippet", "") or "")
    tit_tok, snip_tok = tokenize(title), tokenize(snippet)
    return {
        "rank_in_serp": rank,
        "term": term,
        "link": link,
        "domain": _domain_from_url(link),
        "title": title if len(title) <= 200 else title[:197] + "...",
        "snippet_preview": _snippet_preview(snippet),
        "title_vs_corpus_jaccard": round(jaccard(tit_tok, corpus_tokens), 4),
        "snippet_vs_corpus_jaccard": round(jaccard(snip_tok, corpus_tokens), 4),
    }


def iter_selected_hit_debug_rows(
    term: str,
    raw_results: List[Dict[str, Any]],
    seen_links: set[str],
    *,
    corpus_tokens: frozenset[str],
) -> Iterable[Dict[str, Any]]:
    for rank, result in enumerate(raw_results, start=1):
        row = _hit_row_if_selected(rank, term, result, seen_links, corpus_tokens)
        if row is not None:
            yield row


def _terms_report_summary(terms_report: Dict[str, Any]) -> dict[str, Any]:
    return {
        "used_cache": terms_report.get("used_cache"),
        "cache_hit": terms_report.get("cache_hit"),
        "parse_mode": terms_report.get("parse_mode"),
        "terms_hash": terms_report.get("terms_hash"),
    }


def _per_term_metric_rows(
    search_terms: List[str], corpus_tokens: frozenset[str], title_tokens: frozenset[str]
) -> List[dict[str, Any]]:
    return [
        {"term": t, **term_overlap_metrics(t, corpus_tokens=corpus_tokens, title_tokens=title_tokens)}
        for t in search_terms
    ]


def _build_terms_payload(
    *,
    trace_id: str,
    post_id: Any,
    search_terms: List[str],
    terms_report: Dict[str, Any],
    post_title: Optional[str],
    post_text: Optional[str],
) -> dict[str, Any]:
    title, body = post_title or "", post_text or ""
    corpus_tokens = tokenize(f"{title}\n{body}")
    title_tokens = tokenize(title)
    rows = _per_term_metric_rows(search_terms, corpus_tokens, title_tokens)
    payload = _base_record(trace_id=trace_id, message="research_debug_terms")
    upd = {
        "post_id": post_id,
        "event": "research_relevance_debug",
        "search_terms": search_terms,
        "terms_report": _terms_report_summary(terms_report),
        "corpus_token_count": len(corpus_tokens),
        "per_term_metrics": rows,
    }
    payload.update(upd)
    return payload


def write_research_terms_debug(
    *,
    log_dir: Path,
    trace_id: str,
    post_id: Any,
    search_terms: List[str],
    terms_report: Dict[str, Any],
    post_title: Optional[str],
    post_text: Optional[str],
) -> None:
    payload = _build_terms_payload(
        trace_id=trace_id,
        post_id=post_id,
        search_terms=search_terms,
        terms_report=terms_report,
        post_title=post_title,
        post_text=post_text,
    )
    _append_jsonl(log_dir / "research_terms.jsonl", payload)


def _collect_selected_hit_rows(
    search_terms: List[str],
    raw_results_by_term: List[List[Dict[str, Any]]],
    corpus_tokens: frozenset[str],
) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    hits: List[Dict[str, Any]] = []
    for term, raw in zip(search_terms, raw_results_by_term):
        for row in iter_selected_hit_debug_rows(term, raw, seen, corpus_tokens=corpus_tokens):
            hits.append(row)
    return hits


def _mean_snippet_jaccard(hits: List[Dict[str, Any]]) -> float:
    if not hits:
        return 0.0
    overlaps = [float(h["snippet_vs_corpus_jaccard"]) for h in hits]
    return round(sum(overlaps) / len(overlaps), 4)


def write_research_results_debug(
    *,
    log_dir: Path,
    trace_id: str,
    post_id: Any,
    search_terms: List[str],
    raw_results_by_term: List[List[Dict[str, Any]]],
    corpus_tokens: frozenset[str],
    raw_hits_total: int,
    selected_unique_urls: int,
) -> None:
    hits = _collect_selected_hit_rows(search_terms, raw_results_by_term, corpus_tokens)
    payload = _base_record(trace_id=trace_id, message="research_debug_results")
    payload.update(
        {
            "post_id": post_id,
            "event": "research_relevance_debug",
            "raw_hits_total": raw_hits_total,
            "selected_unique_urls": selected_unique_urls,
            "debug_hits_logged": len(hits),
            "mean_snippet_vs_corpus_jaccard": _mean_snippet_jaccard(hits),
            "hits": hits,
        }
    )
    _append_jsonl(log_dir / "research_results.jsonl", payload)
