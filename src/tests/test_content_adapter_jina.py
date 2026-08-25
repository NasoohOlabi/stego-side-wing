"""Tests for the quota-free Jina Reader content fast path."""

from types import SimpleNamespace

from workflows.adapters.content import LocalContentClient


def test_local_content_client_fetches_jina_text_without_llm(monkeypatch):
    response = SimpleNamespace(
        text="Title: Relevant article\n\nMarkdown Content:\nUseful research text.",
        headers={"Content-Type": "text/plain"},
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr(
        "workflows.adapters.content.requests.get",
        lambda url, timeout: response,
    )

    out = LocalContentClient.fetch("https://r.jina.ai/https://example.com/article")

    assert out["result"]["success"] is True
    assert "Useful research text" in out["result"]["text"]


def test_jina_first_routes_publisher_url_through_reader(monkeypatch):
    seen = []
    response = SimpleNamespace(
        text="Title: Publisher article\n\nMarkdown Content:\nUseful research text.",
        headers={"Content-Type": "text/plain"},
        raise_for_status=lambda: None,
    )
    monkeypatch.setenv("WORKFLOW_URL_FETCH_JINA_FIRST", "1")
    monkeypatch.setattr(
        "workflows.adapters.content.requests.get",
        lambda url, timeout: seen.append(url) or response,
    )

    LocalContentClient.fetch("https://example.com/article")

    assert seen == ["https://r.jina.ai/https://example.com/article"]


def test_jina_reader_retries_throttled_response(monkeypatch):
    throttled = SimpleNamespace(
        status_code=429,
        headers={"Retry-After": "1"},
        raise_for_status=lambda: None,
    )
    success = SimpleNamespace(
        status_code=200,
        text="Useful text",
        headers={"Content-Type": "text/plain"},
        raise_for_status=lambda: None,
    )
    responses = iter((throttled, success))
    monkeypatch.setattr("workflows.adapters.content.requests.get", lambda *a, **k: next(responses))
    monkeypatch.setattr("workflows.adapters.content.time.sleep", lambda seconds: None)

    out = LocalContentClient._jina_fetch("https://example.com/article")

    assert out["result"]["text"] == "Useful text"
