"""Characterization tests for integrations/news_api.py.

Plan step 6.3: integrations/ had zero test coverage. fetch_everything is a clean,
synchronous requests.get call with five real branches (placeholder key, success,
API-level error, HTTP error, unexpected exception) and no async/dependency additions
needed to test it.
"""

from typing import Any

import pytest
import requests

from integrations import news_api


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self) -> dict[str, Any]:
        return self._json_body


def test_fetch_everything_short_circuits_on_placeholder_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(news_api, "NEWS_API_KEY", "YOUR_NEWS_API_KEY")

    def _unexpected_call(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("requests.get should not be called with a placeholder key")

    monkeypatch.setattr(news_api.requests, "get", _unexpected_call)

    result = news_api.fetch_everything({"q": "python"})

    assert result["status"] == "error"
    assert result["code"] == "apiKeyMissing"


def test_fetch_everything_returns_articles_on_success(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(news_api, "NEWS_API_KEY", "real-key")
    body = {
        "status": "ok",
        "totalResults": 1,
        "articles": [
            {
                "source": {"id": None, "name": "Example"},
                "author": "A. Writer",
                "title": "Headline",
                "description": "Desc",
                "url": "https://example.com/a",
                "urlToImage": None,
                "publishedAt": "2026-01-01T00:00:00Z",
                "content": "Body",
            }
        ],
    }
    monkeypatch.setattr(
        news_api.requests, "get", lambda url: _FakeResponse(status_code=200, json_body=body)
    )

    result = news_api.fetch_everything({"q": "python"})

    assert result["status"] == "ok"
    assert result["totalResults"] == 1
    assert result["articles"][0]["title"] == "Headline"


def test_fetch_everything_on_api_level_error_loses_the_original_code(
    monkeypatch: pytest.MonkeyPatch,
):
    """The News API can return HTTP 200 with a body-level status='error'.

    fetch_everything parses that into a typed error_data, then does
    ``raise Exception(f"...{error_data['code']}...")`` -- a plain Exception, not
    error_data itself. The outer ``except Exception`` handler has no way to recover the
    structured fields from a plain Exception's message, so it re-wraps as a generic
    ``fetchError`` and the original ``parameterInvalid`` code is lost except as a
    substring of ``message``. Pinned as characterization, not intended behavior: the
    code that builds ``error_data`` reads like it means to return it directly.
    """
    monkeypatch.setattr(news_api, "NEWS_API_KEY", "real-key")
    body = {"status": "error", "code": "parameterInvalid", "message": "bad query"}
    monkeypatch.setattr(
        news_api.requests, "get", lambda url: _FakeResponse(status_code=200, json_body=body)
    )

    result = news_api.fetch_everything({"q": ""})

    assert result["status"] == "error"
    assert result["code"] == "fetchError"
    assert "parameterInvalid" in result["message"]
    assert "bad query" in result["message"]


def test_fetch_everything_returns_typed_error_on_http_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(news_api, "NEWS_API_KEY", "real-key")
    monkeypatch.setattr(
        news_api.requests, "get", lambda url: _FakeResponse(status_code=503, json_body={})
    )

    result = news_api.fetch_everything({"q": "python"})

    assert result["status"] == "error"
    assert result["code"] == "httpError"


def test_fetch_everything_returns_typed_error_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(news_api, "NEWS_API_KEY", "real-key")

    def _raise(url: str) -> Any:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(news_api.requests, "get", _raise)

    result = news_api.fetch_everything({"q": "python"})

    assert result["status"] == "error"
    assert result["code"] == "fetchError"
    assert "connection reset" in result["message"]


def test_fetch_everything_maps_from_date_to_reserved_from_param(monkeypatch: pytest.MonkeyPatch):
    """``from`` is a Python keyword, so callers use ``from_date``; the URL must carry ``from``."""
    monkeypatch.setattr(news_api, "NEWS_API_KEY", "real-key")
    captured_urls: list[str] = []

    def _capture(url: str) -> _FakeResponse:
        captured_urls.append(url)
        return _FakeResponse(
            status_code=200, json_body={"status": "ok", "totalResults": 0, "articles": []}
        )

    monkeypatch.setattr(news_api.requests, "get", _capture)

    news_api.fetch_everything({"q": "python", "from_date": "2026-01-01"})

    assert len(captured_urls) == 1
    assert "from=2026-01-01" in captured_urls[0]
    assert "from_date" not in captured_urls[0]
