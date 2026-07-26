"""Basic no-network behavior tests for the DuckDuckGo adapter."""

import asyncio

from integrations.duckduckgo_api import search_duckduckgo_with_fallback, searchDuckDuckGo


def test_empty_query_short_circuits_without_http() -> None:
    assert asyncio.run(searchDuckDuckGo("   ")) == {"organic_results": []}


def test_fallback_preserves_an_empty_result(monkeypatch) -> None:
    async def _empty(*_args, **_kwargs):
        return {"organic_results": []}

    monkeypatch.setattr("integrations.duckduckgo_api.searchDuckDuckGo", _empty)
    assert asyncio.run(search_duckduckgo_with_fallback("query")) == {"organic_results": []}
