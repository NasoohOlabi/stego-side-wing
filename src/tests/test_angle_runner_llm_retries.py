"""Tests for angles LLM retries and transport splitting (via public entrypoints)."""

from unittest.mock import MagicMock

import pytest
import requests

from pipelines.angles.angle_runner import analyze_angles_from_texts


def test_analyze_angles_retries_read_timeout_then_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    ok_json = {"choices": [{"message": {"content": "[]"}}]}

    def fake_post(*_a: object, **_k: object) -> MagicMock:
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ReadTimeout("t")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = ok_json
        return resp

    monkeypatch.setattr(
        "pipelines.angles.angle_runner.requests.post",
        fake_post,
    )
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_max_attempts", lambda: 5)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("pipelines.angles.angle_runner.time.sleep", lambda _s: None)

    out = analyze_angles_from_texts(["hello world"], use_cache=False)
    assert out == []
    assert calls["n"] == 3


def test_analyze_angles_connection_error_splits_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large multi-segment input triggers transport split when the LLM drops the connection."""
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_max_attempts", lambda: 2)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("pipelines.angles.angle_runner.time.sleep", lambda _s: None)
    monkeypatch.setattr("pipelines.angles.angle_runner._max_transport_split_depth", lambda: 8)

    ok_row = (
        '[{"source_quote":"q","tangent":"t","category":"c"}]'
    )

    def fake_call_llm(prompt: str) -> str:
        if len(prompt) > 25_000:
            raise requests.exceptions.ConnectionError("drop")
        return ok_row

    monkeypatch.setattr("pipelines.angles.angle_runner._call_llm", fake_call_llm)

    chunk = "word " * 3000
    rows = analyze_angles_from_texts([chunk, chunk], use_cache=False)
    assert len(rows) >= 1
    assert rows[0]["category"] == "c"
