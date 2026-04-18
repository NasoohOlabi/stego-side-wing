"""Tests for HTTP-first plain text extraction."""

import httpx
import pytest

from content_acquisition.url_http_extract import fetch_main_text_via_http


def test_fetch_main_text_returns_none_below_min_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def get(self, _url: str) -> object:
            class Resp:
                status_code = 200
                headers = {"content-type": "text/html; charset=utf-8"}
                text = "<html><body><p>short</p></body></html>"

            return Resp()

    monkeypatch.setattr(httpx, "Client", lambda **kw: FakeClient())
    assert fetch_main_text_via_http("https://example.com/a", min_chars=400) is None


def test_fetch_main_text_returns_content_when_long_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    body_inner = ("word " * 120) + "<p>done</p>"
    html = f"<html><body><article>{body_inner}</article></body></html>"

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def get(self, _url: str) -> object:
            class Resp:
                status_code = 200
                headers = {"content-type": "text/html"}
                text = html

            return Resp()

    monkeypatch.setattr(httpx, "Client", lambda **kw: FakeClient())
    out = fetch_main_text_via_http("https://example.com/b", min_chars=400, timeout_sec=10.0)
    assert out is not None
    assert len(out) >= 400
