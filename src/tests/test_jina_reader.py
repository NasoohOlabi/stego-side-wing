"""Tests for Jina Reader client and fetch_url_content_crawl4ai Jina-first behavior."""
from unittest.mock import MagicMock, patch

import pytest

from integrations import jina_reader
from services import analysis_service


@pytest.fixture
def no_url_cache(monkeypatch):
    monkeypatch.setattr("infrastructure.cache.read_json_cache", lambda p: None)
    monkeypatch.setattr("infrastructure.cache.write_json_cache", lambda p, d: None)


@patch("integrations.jina_reader.httpx.Client")
def test_fetch_jina_reader_markdown_success(mock_client_cls):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "  # Hi\n\nok  "
    mock_instance = MagicMock()
    mock_instance.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_instance

    assert jina_reader.fetch_jina_reader_markdown("https://example.com/x") == "# Hi\n\nok"


@patch("integrations.jina_reader.httpx.Client")
def test_fetch_jina_reader_markdown_none_on_status(mock_client_cls):
    mock_response = MagicMock()
    mock_response.status_code = 502
    mock_instance = MagicMock()
    mock_instance.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_instance

    assert jina_reader.fetch_jina_reader_markdown("https://example.com/x") is None


def test_try_jina_reader_result_returns_dict():
    with patch.object(
        jina_reader,
        "fetch_jina_reader_markdown",
        return_value="md",
    ):
        r = jina_reader.try_jina_reader_result("https://example.com/")
        assert r == {"raw_content": "md", "source": "jina_reader"}


def test_fetch_url_content_prefers_jina(no_url_cache, monkeypatch):
    monkeypatch.setattr(
        analysis_service,
        "try_jina_reader_result",
        lambda u: {"raw_content": "body", "source": "jina_reader"},
    )

    def boom(_c):
        raise AssertionError("crawl4ai must not run when Jina succeeds")

    monkeypatch.setattr(analysis_service, "run_async", boom)

    out = analysis_service.fetch_url_content_crawl4ai("https://example.com/a")
    assert out["result"]["raw_content"] == "body"


def test_fetch_url_content_fallback_crawl4ai(no_url_cache, monkeypatch):
    monkeypatch.setattr(analysis_service, "try_jina_reader_result", lambda u: None)

    def fake_run_async(coro):
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
