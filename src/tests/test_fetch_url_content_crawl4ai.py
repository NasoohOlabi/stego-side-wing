"""Tests for fetch_url_content_crawl4ai (crawl4ai path on cache miss)."""

from collections.abc import Coroutine
from typing import Any

import pytest

from services import analysis_service


@pytest.fixture
def no_url_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("infrastructure.cache.read_json_cache", lambda p: None)
    monkeypatch.setattr("infrastructure.cache.write_json_cache", lambda p, d: None)


def test_fetch_url_content_crawl4ai_on_miss(
    monkeypatch: pytest.MonkeyPatch,
    no_url_cache: None,
) -> None:
    def fake_run_async(coro: Coroutine[Any, Any, Any]) -> dict[str, Any]:
        coro.close()
        return {
            "title": "t",
            "summary": "s",
            "key_points": ["k"],
            "author": "a",
        }

    monkeypatch.setattr(analysis_service, "run_async", fake_run_async)

    out = analysis_service.fetch_url_content_crawl4ai("https://example.com/b")
    assert out["result"]["title"] == "t"
