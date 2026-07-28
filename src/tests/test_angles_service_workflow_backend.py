"""Angles service dispatches to workflow LLM (Google) vs LM Studio by env."""

from pathlib import Path

import httpx
import pytest
import requests

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.cache_context import angles_cache_context


@pytest.mark.usefixtures("clear_llm_backend_env")
def test_analyze_angles_google_backend_uses_llm_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")
    calls: list[dict[str, object]] = []

    def fake_call_llm(self: LLMAdapter, *args: object, **kwargs: object) -> str:
        calls.append(kwargs)
        return '[{"source_quote":"a","tangent":"b","category":"c"}]'

    monkeypatch.setattr(LLMAdapter, "call_llm", fake_call_llm)

    cache_root = tmp_path / "angles_wf_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    with angles_cache_context(cache_root):
        from services.angles_service import analyze_angles

        out = analyze_angles(["hello block"], use_cache=False)

    assert len(out) == 1
    assert out[0]["source_quote"] == "a"
    assert out[0]["tangent"] == "b"
    assert out[0]["category"] == "c"
    assert out[0].get("source_document") == 0
    assert len(calls) >= 1
    assert calls[0].get("system_message") is None


def test_analyze_angles_lm_backend_uses_angle_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    seen: list[tuple[list[str], bool]] = []

    def track(
        texts: list[str],
        *,
        use_cache: bool = True,
        max_results: int | None = None,
    ) -> list[dict[str, str]]:
        assert max_results is None
        seen.append((list(texts), use_cache))
        return []

    monkeypatch.setattr("services.angles_service.analyze_angles_from_texts", track)
    from services.angles_service import analyze_angles

    analyze_angles(["only_lm"], use_cache=False)
    assert seen == [(["only_lm"], False)]


def test_analyze_angles_propagates_max_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int | None] = []

    def track(
        _texts: list[str],
        *,
        use_cache: bool = True,
        max_results: int | None = None,
    ) -> list[dict[str, str]]:
        assert use_cache is False
        seen.append(max_results)
        return []

    monkeypatch.setattr("services.angles_service.analyze_angles_from_texts", track)
    from services.angles_service import analyze_angles

    analyze_angles(["bounded"], use_cache=False, max_results=17)

    assert seen == [17]


def test_backend_clients_propagate_max_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int | None]] = []

    class _Local:
        def analyze_angles(
            self,
            _texts: list[str],
            *,
            use_cache: bool = True,
            max_results: int | None = None,
        ) -> dict[str, object]:
            calls.append(("adapter", max_results))
            return {"results": []}

    adapter = BackendAPIAdapter.__new__(BackendAPIAdapter)
    adapter.local = _Local()  # type: ignore[assignment]
    adapter.analyze_angles(["a"], max_results=11)

    def analyze(
        _texts: object,
        *,
        use_cache: bool = True,
        max_results: int | None = None,
    ) -> list[dict[str, str]]:
        calls.append(("client", max_results))
        return []

    monkeypatch.setattr("services.angles_service.analyze_angles", analyze)
    from services.workflow_backend_client import LocalBackendClient

    client = LocalBackendClient(object())  # type: ignore[arg-type]
    client.analyze_angles(["b"], max_results=13)

    assert calls == [("adapter", 11), ("client", 13)]


@pytest.mark.usefixtures("clear_llm_backend_env")
def test_analyze_angles_google_disk_cache_avoids_repeat_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")
    calls = {"n": 0}

    def fake_call_llm(self: LLMAdapter, *args: object, **kwargs: object) -> str:
        calls["n"] += 1
        return '[{"source_quote":"x","tangent":"y","category":"z"}]'

    monkeypatch.setattr(LLMAdapter, "call_llm", fake_call_llm)
    cache_root = tmp_path / "angles_wf_cache2"
    cache_root.mkdir(parents=True, exist_ok=True)
    with angles_cache_context(cache_root):
        from services.angles_service import analyze_angles

        text = "same text for cache key"
        r1 = analyze_angles([text], use_cache=True)
        r2 = analyze_angles([text], use_cache=True)

    assert calls["n"] == 1
    assert r1 == r2
    wf_dir = cache_root / "workflow_google"
    assert wf_dir.is_dir()
    assert any(wf_dir.glob("*.json"))


@pytest.mark.usefixtures("clear_llm_backend_env")
def test_analyze_angles_google_transport_split_recovers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._effective_max_chars_per_prompt",
        lambda: 100_000,
    )
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._max_transport_split_depth",
        lambda: 8,
    )
    calls: list[int] = []

    def fake_call_llm(self: LLMAdapter, *args: object, **kwargs: object) -> str:
        prompt = str(kwargs["prompt"])
        calls.append(len(prompt))
        if len(prompt) > 25_000:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return '[{"source_quote":"a","tangent":"b","category":"c"}]'

    monkeypatch.setattr(LLMAdapter, "call_llm", fake_call_llm)

    cache_root = tmp_path / "angles_wf_split_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    with angles_cache_context(cache_root):
        from services.angles_service import analyze_angles

        text = "word " * 6000
        out = analyze_angles([text], use_cache=False)

    assert len(out) >= 1
    assert out[0]["category"] == "c"
    assert out[0]["source_document"] == 0
    assert len(calls) >= 3
    assert max(calls) > 25_000
    assert min(calls) < max(calls)


@pytest.mark.usefixtures("clear_llm_backend_env")
def test_analyze_angles_google_transport_split_recovers_short_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._effective_max_chars_per_prompt",
        lambda: 100_000,
    )
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._max_transport_split_depth",
        lambda: 8,
    )
    calls: list[int] = []

    def fake_call_llm(self: LLMAdapter, *args: object, **kwargs: object) -> str:
        prompt = str(kwargs["prompt"])
        calls.append(len(prompt))
        if len(prompt) > 2_000:
            raise requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected("
                "'Remote end closed connection without response'))"
            )
        return '[{"source_quote":"a","tangent":"b","category":"c"}]'

    monkeypatch.setattr(LLMAdapter, "call_llm", fake_call_llm)

    cache_root = tmp_path / "angles_wf_split_short_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    with angles_cache_context(cache_root):
        from services.angles_service import analyze_angles

        text = "word " * 280
        out = analyze_angles([text], use_cache=False)

    assert len(out) >= 1
    assert out[0]["category"] == "c"
    assert out[0]["source_document"] == 0
    assert len(calls) >= 3
    assert max(calls) > 2_000
    assert min(calls) < 2_000


@pytest.mark.usefixtures("clear_llm_backend_env")
def test_analyze_angles_google_transport_split_recovers_very_short_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "ai_studio")
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._effective_max_chars_per_prompt",
        lambda: 100_000,
    )
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._max_transport_split_depth",
        lambda: 8,
    )
    calls: list[int] = []

    def fake_call_llm(self: LLMAdapter, *args: object, **kwargs: object) -> str:
        prompt = str(kwargs["prompt"])
        calls.append(len(prompt))
        if len(prompt) > 1_500:
            raise requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected("
                "'Remote end closed connection without response'))"
            )
        return '[{"source_quote":"a","tangent":"b","category":"c"}]'

    monkeypatch.setattr(LLMAdapter, "call_llm", fake_call_llm)

    cache_root = tmp_path / "angles_wf_split_very_short_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    with angles_cache_context(cache_root):
        from services.angles_service import analyze_angles

        text = "word " * 55
        out = analyze_angles([text], use_cache=False)

    assert len(out) >= 1
    assert out[0]["category"] == "c"
    assert out[0]["source_document"] == 0
    assert len(calls) >= 3
    assert max(calls) > 1_500
    assert min(calls) < 1_500


def test_angles_system_prompt_is_clean_and_machine_readable() -> None:
    from workflows.utils.angles_llm_config import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()

    assert not SYSTEM_PROMPT.endswith("\n")
    assert "kill myself" not in lowered
    assert "json validator" not in lowered
    assert "source_quote" in SYSTEM_PROMPT
    assert "tangent" in SYSTEM_PROMPT
    assert "category" in SYSTEM_PROMPT
