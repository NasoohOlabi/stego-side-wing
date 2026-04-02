"""Jina Reader (r.jina.ai) — fetch page content as markdown via HTTP."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx
from pydantic import validate_call

from infrastructure.config import get_env

logger = logging.getLogger(__name__)


def _reader_base() -> str:
    return (get_env("JINA_READER_BASE") or "https://r.jina.ai").rstrip("/")


def _build_reader_url(target_url: str) -> str:
    print(f"Target URL: {target_url}")
    print(f"Reader base: {_reader_base()}")
    print(f"Quote: {quote(target_url, safe='')}")
    print(f"Reader URL: {f'{_reader_base()}/{quote(target_url, safe="")}'}")
    return f"{_reader_base()}/{quote(target_url, safe='')}"


def _request_headers() -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "text/plain, text/markdown, */*",
        "User-Agent": "stego-side-wing/0.1",
    }
    key = get_env("JINA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


@validate_call
def fetch_jina_reader_markdown(target_url: str) -> Optional[str]:
    """Return markdown body from Jina Reader, or None on failure or empty body."""
    reader_url = _build_reader_url(target_url)
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(reader_url, headers=_request_headers())
    except httpx.HTTPError as exc:
        logger.warning(
            "jina_reader_http_error",
            extra={
                "event": "analysis",
                "action": "jina_reader_fetch",
                "url": target_url,
                "error": str(exc),
            },
        )
        return None

    if response.status_code != 200:
        logger.warning(
            "jina_reader_bad_status",
            extra={
                "event": "analysis",
                "action": "jina_reader_fetch",
                "url": target_url,
                "status_code": response.status_code,
            },
        )
        return None

    text = response.text.strip()
    if not text:
        logger.warning(
            "jina_reader_empty_body",
            extra={
                "event": "analysis",
                "action": "jina_reader_fetch",
                "url": target_url,
            },
        )
        return None
    return text


def try_jina_reader_result(target_url: str) -> Optional[Dict[str, Any]]:
    """Structured result compatible with crawl4ai partial success, or None."""
    md = fetch_jina_reader_markdown(target_url)
    if md is None:
        return None
    return {"raw_content": md, "source": "jina_reader"}
