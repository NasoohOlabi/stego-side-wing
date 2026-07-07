"""Plain HTTP + HTML text extraction before launching a headless browser crawl."""

from typing import Any

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, validate_call

from infrastructure.config import get_workflow_url_fetch_http_timeout_sec

USER_AGENT = "Mozilla/5.0 (compatible; stego-side-wing/0.1; +https://example.invalid)"


class _HttpExtractArgs(BaseModel):
    """Validated inputs for :func:`fetch_main_text_via_http`."""

    url: str = Field(min_length=1)
    timeout_sec: float
    min_chars: int


def _readable_body_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    root: Any = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text(separator="\n", strip=True)
    lines = [ln for ln in (x.strip() for x in text.splitlines()) if ln]
    return "\n".join(lines)


@validate_call
def fetch_main_text_via_http(
    url: str,
    *,
    timeout_sec: float | None = None,
    min_chars: int = 400,
) -> str | None:
    """
    GET ``url`` and return main visible text if length >= ``min_chars``, else None.

    Uses a generic browser User-Agent. Failures (non-200, non-HTML, short text)
    return None so callers can fall back to Crawl4AI.
    """
    resolved = timeout_sec if timeout_sec is not None else get_workflow_url_fetch_http_timeout_sec()
    _HttpExtractArgs(url=url, timeout_sec=resolved, min_chars=min_chars)
    u = url.strip()
    if not u:
        return None

    try:
        with httpx.Client(
            timeout=resolved,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = client.get(u)
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None
    ct = (resp.headers.get("content-type") or "").lower()
    if "html" not in ct and "text/plain" not in ct:
        return None
    raw = resp.text or ""
    if len(raw) < 50:
        return None
    plain = _readable_body_text(raw).strip()
    if len(plain) < min_chars:
        return None
    return plain
