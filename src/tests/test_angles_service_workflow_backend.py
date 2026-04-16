"""Angles service dispatches to workflow LLM (Google) vs LM Studio by env."""

from pathlib import Path

import pytest

import infrastructure.config as infra_config
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
    assert calls[0].get("system_message")


def test_analyze_angles_lm_backend_uses_angle_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    seen: list[tuple[list[str], bool]] = []

    def track(texts: list[str], *, use_cache: bool = True) -> list[dict[str, str]]:
        seen.append((list(texts), use_cache))
        return []

    monkeypatch.setattr("services.angles_service.analyze_angles_from_texts", track)
    from services.angles_service import analyze_angles

    analyze_angles(["only_lm"], use_cache=False)
    assert seen == [(["only_lm"], False)]


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
