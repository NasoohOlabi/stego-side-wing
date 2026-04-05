from unittest.mock import MagicMock

import pytest
import requests

from workflows.adapters.llm import LLMAdapter


def _adapter() -> LLMAdapter:
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.openai_api_key = None
    adapter.google_palm_api_key = None
    adapter.groq_api_key = None
    adapter.lm_studio_url = "https://example.invalid/v1"
    adapter.lm_studio_api_token = "token"
    adapter.lm_studio_timeout_sec = 1
    adapter.last_call_metadata = {}
    return adapter


def test_lm_studio_retries_transient_http_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_post(*_args: object, **_kwargs: object) -> MagicMock:
        calls["n"] += 1
        resp = MagicMock()
        if calls["n"] < 3:
            resp.status_code = 503
            resp.text = "Service Unavailable"
            resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
                "503 Server Error: Service Unavailable",
                response=resp,
            )
            return resp
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    monkeypatch.setattr("workflows.adapters.llm.requests.post", fake_post)
    monkeypatch.setattr("workflows.adapters.llm._llm_max_attempts", lambda: 4)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_jitter_sec", lambda _s: 0.0)
    monkeypatch.setattr("workflows.adapters.llm.time.sleep", lambda _s: None)

    adapter = _adapter()
    out = adapter.call_llm(prompt="hello", provider="lm_studio", model="demo")

    assert out == "ok"
    assert calls["n"] == 3
    assert adapter.last_call_metadata["retry_count"] == 2
    assert adapter.last_call_metadata["success"] is True


def test_lm_studio_404_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_post(*_args: object, **_kwargs: object) -> MagicMock:
        calls["n"] += 1
        resp = MagicMock()
        resp.status_code = 404
        resp.text = "Not Found"
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "404 Client Error: Not Found",
            response=resp,
        )
        return resp

    monkeypatch.setattr("workflows.adapters.llm.requests.post", fake_post)
    monkeypatch.setattr("workflows.adapters.llm._llm_max_attempts", lambda: 4)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_jitter_sec", lambda _s: 0.0)
    monkeypatch.setattr("workflows.adapters.llm.time.sleep", lambda _s: None)

    adapter = _adapter()
    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        adapter.call_llm(prompt="hello", provider="lm_studio", model="demo")

    assert calls["n"] == 1
    assert excinfo.value.response is not None
    assert excinfo.value.response.status_code == 404
    assert adapter.last_call_metadata["retry_count"] == 0
    assert adapter.last_call_metadata["http_status"] == 404
