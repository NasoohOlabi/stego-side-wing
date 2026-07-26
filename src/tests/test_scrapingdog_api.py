"""Characterization tests for the synchronous ScrapingDog adapter."""

from typing import Any

import pytest

from integrations import scrapingdog_api


class _Response:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.body = body

    def json(self) -> dict[str, Any]:
        return self.body


def test_search_google_returns_provider_organic_results(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        scrapingdog_api.requests,
        "get",
        lambda *_args, **_kwargs: _Response(200, {"organic_results": [{"title": "One"}]}),
    )

    assert scrapingdog_api.searchGoogle("query") == [{"title": "One"}]
    assert (tmp_path / "last_response_from_sdg.json").is_file()


def test_search_google_returns_empty_list_for_http_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        scrapingdog_api.requests, "get", lambda *_args, **_kwargs: _Response(503, {})
    )

    assert scrapingdog_api.searchGoogle("query") == []
