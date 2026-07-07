"""Tests for Crawl4AI scraper HTTP-first fast path."""

import asyncio
from collections.abc import Generator

import pytest
from pydantic import BaseModel, Field

from content_acquisition import scraper


class _Article(BaseModel):
    title: str = Field(default="t")
    summary: str = Field(default="s")
    key_points: list[str] = Field(default_factory=list)
    author: str = Field(default="a")


@pytest.fixture(autouse=True)
def reset_crawler_singleton() -> Generator[None]:
    scraper.reset_shared_crawler_for_tests()
    yield
    scraper.reset_shared_crawler_for_tests()


def test_google_backend_http_first_skips_browser_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")

    monkeypatch.setattr(
        scraper,
        "fetch_main_text_via_http",
        lambda url, **kw: "body " * 120,
    )
    monkeypatch.setattr(
        scraper,
        "_sync_llm_schema_extract",
        lambda *_a, **_k: {
            "title": "T",
            "summary": "S",
            "key_points": ["k"],
            "author": "Auth",
        },
    )

    out = asyncio.run(
        scraper.extract_structured_data(
            "https://example.com/page",
            _Article,
            "mistral-nemo-instruct-2407-abliterated",
            "Extract article.",
        )
    )
    assert out is not None
    assert out["title"] == "T"


def test_google_backend_falls_back_when_http_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")
    monkeypatch.setattr(scraper, "fetch_main_text_via_http", lambda url, **kw: None)

    class DummyCrawler:
        def __init__(self) -> None:
            self.arun_calls = 0

        async def arun(self, url: str, config: object) -> object:
            self.arun_calls += 1

            class Res:
                success = True
                error_message = None

            return Res()

    dummy = DummyCrawler()

    async def fake_shared() -> DummyCrawler:
        return dummy

    monkeypatch.setattr(scraper, "_shared_crawler_instance", fake_shared)
    monkeypatch.setattr(scraper, "_page_text_fallback", lambda _r: "paragraph " * 80)
    monkeypatch.setattr(
        scraper,
        "_sync_llm_schema_extract",
        lambda *_a, **_k: {
            "title": "X",
            "summary": "Y",
            "key_points": ["z"],
            "author": "Z",
        },
    )

    out = asyncio.run(
        scraper.extract_structured_data(
            "https://example.com/page2",
            _Article,
            "mistral-nemo-instruct-2407-abliterated",
            "Extract article.",
        )
    )
    assert out is not None and out["title"] == "X"
    assert dummy.arun_calls == 1
