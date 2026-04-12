"""Tests for angles LLM retries and transport splitting (via public entrypoints)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from infrastructure.cache import deterministic_hash_sha256
from pipelines.angles.angle_runner import analyze_angles_from_texts, angles_model_name


@pytest.fixture(autouse=True)
def _angles_http_tests_use_lm_studio(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests mock ``requests.post`` on the legacy LM Studio HTTP path."""
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")


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


def test_analyze_angles_retries_retryable_http_status_then_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    ok_json = {"choices": [{"message": {"content": "[]"}}]}

    def fake_post(*_a: object, **_k: object) -> MagicMock:
        calls["n"] += 1
        resp = MagicMock()
        if calls["n"] < 3:
            resp.status_code = 503
            resp.text = "Service Unavailable"
            return resp
        resp.status_code = 200
        resp.json.return_value = ok_json
        return resp

    monkeypatch.setattr("pipelines.angles.angle_runner.requests.post", fake_post)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_max_attempts", lambda: 4)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("pipelines.angles.angle_runner.time.sleep", lambda _s: None)

    out = analyze_angles_from_texts(["hello world"], use_cache=False)
    assert out == []
    assert calls["n"] == 3


def test_analyze_angles_exhausts_retryable_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_post(*_a: object, **_k: object) -> MagicMock:
        calls["n"] += 1
        resp = MagicMock()
        resp.status_code = 503
        resp.text = "Service Unavailable"
        return resp

    monkeypatch.setattr("pipelines.angles.angle_runner.requests.post", fake_post)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_max_attempts", lambda: 2)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("pipelines.angles.angle_runner.time.sleep", lambda _s: None)

    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        analyze_angles_from_texts(["hello world"], use_cache=False)

    assert calls["n"] == 2
    assert excinfo.value.response is not None
    assert excinfo.value.response.status_code == 503


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
    assert rows[0]["source_document"] == 0


def test_analyze_angles_quarantines_corrupt_cache_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("pipelines.angles.angle_runner.get_angles_cache_dir", lambda: tmp_path)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_max_attempts", lambda: 1)
    monkeypatch.setattr("pipelines.angles.angle_runner._llm_retry_backoff_sec", lambda _i: 0.0)
    monkeypatch.setattr("pipelines.angles.angle_runner.time.sleep", lambda _s: None)

    text = "hello world"
    cache_key = deterministic_hash_sha256(text)
    cache_file = tmp_path / f"{cache_key}.json"
    cache_file.write_text('{"oops": true}', encoding="utf-8")

    def fake_call_llm(_prompt: str) -> str:
        return '[{"source_quote":"q","tangent":"t","category":"c"}]'

    monkeypatch.setattr("pipelines.angles.angle_runner._call_llm", fake_call_llm)

    rows = analyze_angles_from_texts([text], use_cache=True)

    assert rows[0]["category"] == "c"
    quarantine_dir = tmp_path / "_quarantine"
    assert quarantine_dir.exists()
    assert any(item.name.startswith(cache_key) for item in quarantine_dir.iterdir())


def test_analyze_angles_sets_source_document_per_text_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_call_llm(_prompt: str) -> str:
        return '[{"source_quote":"q","tangent":"t","category":"c"}]'

    monkeypatch.setattr("pipelines.angles.angle_runner._call_llm", fake_call_llm)
    out = analyze_angles_from_texts(["first block", "second block"], use_cache=False)
    assert len(out) == 2
    assert out[0]["source_document"] == 0
    assert out[1]["source_document"] == 1


def test_angles_model_name_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANGLES_MODEL", raising=False)
    monkeypatch.setenv("MODEL", "mistral-test")
    assert angles_model_name() == "mistral-test"
    monkeypatch.setenv("ANGLES_MODEL", "override-model")
    assert angles_model_name() == "override-model"
