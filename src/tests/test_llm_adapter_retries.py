from unittest.mock import MagicMock

import httpx
import pytest
import requests

from workflows.adapters.llm import LLMAdapter


def _adapter() -> LLMAdapter:
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.openai_api_key = None
    adapter.google_palm_api_key = None
    adapter.google_generative_language_api_keys = []
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


def test_gemini_rotates_to_second_key_after_403(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []

    class FakeModels:
        def __init__(self, api_key: str) -> None:
            self._api_key = api_key

        def generate_content(self, **_kwargs: object) -> MagicMock:
            calls.append(self._api_key)
            if self._api_key == "key-a":
                from google.genai.errors import ClientError

                raise ClientError(
                    403,
                    {"error": {"reason": "API_KEY_SERVICE_BLOCKED"}},
                    None,
                )
            ok = MagicMock()
            ok.text = "ok"
            return ok

    class FakeClient:
        def __init__(self, api_key: str | None = None, **_kwargs: object) -> None:
            self._api_key = api_key or ""

        @property
        def models(self) -> FakeModels:
            return FakeModels(self._api_key)

    monkeypatch.setattr("workflows.adapters.llm.genai.Client", FakeClient)
    monkeypatch.setattr("workflows.adapters.llm._llm_max_attempts", lambda: 2)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_jitter_sec", lambda _s: 0.0)
    monkeypatch.setattr("workflows.adapters.llm.time.sleep", lambda _s: None)

    adapter = _adapter()
    adapter.google_generative_language_api_keys = ["key-a", "key-b"]
    adapter.google_palm_api_key = "key-a"
    out = adapter.call_llm(prompt="hi", provider="gemini", model="gemini-pro")
    assert out == "ok"
    assert calls == ["key-a", "key-b"]


def test_gemini_retries_remote_protocol_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_genai_generate_text(**_kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        return "ok"

    monkeypatch.setattr(
        "workflows.adapters.llm._genai_generate_text",
        fake_genai_generate_text,
    )
    monkeypatch.setattr("workflows.adapters.llm._llm_max_attempts", lambda: 3)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_jitter_sec", lambda _s: 0.0)
    monkeypatch.setattr("workflows.adapters.llm.time.sleep", lambda _s: None)

    adapter = _adapter()
    adapter.google_generative_language_api_keys = ["key-a"]
    adapter.google_palm_api_key = "key-a"

    out = adapter.call_llm(prompt="hi", provider="gemini", model="gemini-pro")

    assert out == "ok"
    assert calls["n"] == 2
    assert adapter.last_call_metadata["retry_count"] == 1
    assert adapter.last_call_metadata["success"] is True


def test_gemini_rotates_to_second_key_after_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_genai_generate_text(*, api_key: str, **_kwargs: object) -> str:
        calls.append(api_key)
        if api_key == "key-a":
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        return "ok"

    monkeypatch.setattr(
        "workflows.adapters.llm._genai_generate_text",
        fake_genai_generate_text,
    )
    monkeypatch.setattr("workflows.adapters.llm._llm_max_attempts", lambda: 1)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_jitter_sec", lambda _s: 0.0)
    monkeypatch.setattr("workflows.adapters.llm.time.sleep", lambda _s: None)

    adapter = _adapter()
    adapter.google_generative_language_api_keys = ["key-a", "key-b"]
    adapter.google_palm_api_key = "key-a"

    out = adapter.call_llm(prompt="hi", provider="gemini", model="gemini-pro")

    assert out == "ok"
    assert calls == ["key-a", "key-b"]


def test_gemini_retries_without_system_message_after_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_genai_generate_text(
        *, api_key: str, system_message: str | None, **_kwargs: object
    ) -> str:
        calls.append((api_key, system_message))
        if system_message is not None:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        return "ok"

    monkeypatch.setattr(
        "workflows.adapters.llm._genai_generate_text",
        fake_genai_generate_text,
    )
    monkeypatch.setattr("workflows.adapters.llm._llm_max_attempts", lambda: 1)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_jitter_sec", lambda _s: 0.0)
    monkeypatch.setattr("workflows.adapters.llm.time.sleep", lambda _s: None)

    adapter = _adapter()
    adapter.google_generative_language_api_keys = ["key-a"]
    adapter.google_palm_api_key = "key-a"

    out = adapter.call_llm(
        prompt="hi",
        system_message="Return JSON only.",
        provider="gemini",
        model="gemini-pro",
    )

    assert out == "ok"
    assert calls == [("key-a", "Return JSON only."), ("key-a", None)]


def test_gemini_system_message_fallback_can_rotate_to_second_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_genai_generate_text(
        *, api_key: str, system_message: str | None, **_kwargs: object
    ) -> str:
        calls.append((api_key, system_message))
        if api_key == "key-a":
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        if system_message is not None:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        return "ok"

    monkeypatch.setattr(
        "workflows.adapters.llm._genai_generate_text",
        fake_genai_generate_text,
    )
    monkeypatch.setattr("workflows.adapters.llm._llm_max_attempts", lambda: 1)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("workflows.adapters.llm._llm_retry_jitter_sec", lambda _s: 0.0)
    monkeypatch.setattr("workflows.adapters.llm.time.sleep", lambda _s: None)

    adapter = _adapter()
    adapter.google_generative_language_api_keys = ["key-a", "key-b"]
    adapter.google_palm_api_key = "key-a"

    out = adapter.call_llm(
        prompt="hi",
        system_message="Return JSON only.",
        provider="gemini",
        model="gemini-pro",
    )

    assert out == "ok"
    assert calls == [
        ("key-a", "Return JSON only."),
        ("key-a", None),
        ("key-b", "Return JSON only."),
        ("key-b", None),
    ]


def test_gemini_client_uses_default_sdk_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int | None] = []

    class FakeModels:
        def generate_content(self, **_kwargs: object) -> MagicMock:
            ok = MagicMock()
            ok.text = "ok"
            return ok

    class FakeClient:
        def __init__(
            self,
            api_key: str | None = None,
            http_options: object | None = None,
            **_kwargs: object,
        ) -> None:
            _ = api_key
            seen.append(getattr(http_options, "timeout", None))

        @property
        def models(self) -> FakeModels:
            return FakeModels()

    monkeypatch.setattr("workflows.adapters.llm.genai.Client", FakeClient)

    adapter = _adapter()
    adapter.google_generative_language_api_keys = ["key-a"]
    adapter.google_palm_api_key = "key-a"

    out = adapter.call_llm(prompt="hi", provider="gemini", model="gemini-pro")

    assert out == "ok"
    assert seen == [None]


def test_gemini_transport_uses_rest_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeModels:
        def generate_content(self, **_kwargs: object) -> MagicMock:
            raise httpx.ConnectTimeout("_ssl.c:1011: The handshake operation timed out")

    class FakeClient:
        def __init__(
            self,
            api_key: str | None = None,
            http_options: object | None = None,
            **_kwargs: object,
        ) -> None:
            _ = (api_key, http_options)

        @property
        def models(self) -> FakeModels:
            return FakeModels()

    def fake_post(*_args: object, **kwargs: object) -> MagicMock:
        payload = kwargs["json"]
        assert isinstance(payload, dict)
        assert kwargs["params"] == {"key": "key-a"}
        assert kwargs["timeout"] == 30
        assert payload["system_instruction"]["parts"][0]["text"] == "Return JSON only."
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "internal reasoning", "thought": True},
                            {"text": '{"ok":true}'},
                        ]
                    }
                }
            ]
        }
        return resp

    monkeypatch.setattr("workflows.adapters.llm.genai.Client", FakeClient)
    monkeypatch.setattr("workflows.adapters.llm.requests.post", fake_post)

    adapter = _adapter()
    adapter.google_generative_language_api_keys = ["key-a"]
    adapter.google_palm_api_key = "key-a"

    out = adapter.call_llm(
        prompt="hi",
        system_message="Return JSON only.",
        provider="gemini",
        model="gemini-pro",
    )

    assert out == '{"ok":true}'
