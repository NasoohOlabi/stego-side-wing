import json
from io import StringIO
from pathlib import Path

import pytest

from content_acquisition.angles.angle_runner import (
    MalformedAnglesResponseError,
    _emit_status,
    _parse_or_repair_workflow,
    analyze_angles_from_texts,
    analyze_angles_from_texts_via_workflow_llm,
)
from infrastructure.cache import deterministic_hash_sha256


class _AsciiOnlyStream(StringIO):
    def write(self, s):
        if any(ord(ch) > 127 for ch in s):
            raise UnicodeEncodeError("ascii", s, 0, 1, "ordinal not in range(128)")
        return super().write(s)


def test_emit_status_falls_back_to_ascii(monkeypatch):
    stream = _AsciiOnlyStream()
    monkeypatch.setattr("content_acquisition.angles.angle_runner.sys.stdout", stream)

    _emit_status("cache hit 📂")

    assert stream.getvalue() == "cache hit ?\n"


class _FakeLLMEmptyRepair:
    def call_llm(self, **_kwargs: object) -> str:
        return ""


def test_parse_or_repair_workflow_empty_repair_raises() -> None:
    with pytest.raises(ValueError, match="empty text after JSON repair"):
        _parse_or_repair_workflow(
            _FakeLLMEmptyRepair(),
            provider="p",
            model="m",
            raw_text="not json",
        )


def test_parse_or_repair_workflow_empty_schema_repair_raises() -> None:
    class _FakeLLMSchemaRepairEmpty:
        def call_llm(self, **_kwargs: object) -> str:
            return ""

    with pytest.raises(ValueError, match="empty text after JSON schema repair"):
        _parse_or_repair_workflow(
            _FakeLLMSchemaRepairEmpty(),
            provider="p",
            model="m",
            raw_text="[1]",
        )


def test_analyze_angles_stops_at_raw_target(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _run(batch: list[str], _index: int, _total: int) -> list[dict[str, str]]:
        calls.append(batch[0])
        return [{"source_quote": batch[0], "tangent": "t", "category": "c"}]

    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    monkeypatch.setattr("content_acquisition.angles.angle_runner._run_angle_llm_on_batch", _run)

    result = analyze_angles_from_texts(
        ["one", "two", "three"],
        use_cache=False,
        max_results=2,
    )

    assert len(result) == 2
    assert calls == ["one", "two"]


def test_analyze_angles_isolates_one_malformed_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(batch: list[str], _index: int, _total: int) -> list[dict[str, str]]:
        if batch[0] == "bad":
            raise MalformedAnglesResponseError("malformed response")
        return [{"source_quote": batch[0], "tangent": "t", "category": "c"}]

    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    monkeypatch.setattr("content_acquisition.angles.angle_runner._run_angle_llm_on_batch", _run)

    result = analyze_angles_from_texts(["one", "bad", "three"], use_cache=False)

    assert [row["source_quote"] for row in result] == ["one", "three"]
    assert [row["source_document"] for row in result] == [0, 2]


def test_analyze_angles_raises_when_every_block_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(_batch: list[str], _index: int, _total: int) -> list[dict[str, str]]:
        raise MalformedAnglesResponseError("malformed response")

    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    monkeypatch.setattr("content_acquisition.angles.angle_runner._run_angle_llm_on_batch", _run)

    with pytest.raises(ValueError, match="malformed response"):
        analyze_angles_from_texts(["one", "two"], use_cache=False)


def test_analyze_angles_provider_value_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(_batch: list[str], _index: int, _total: int) -> list[dict[str, str]]:
        raise ValueError("provider configuration failed")

    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    monkeypatch.setattr("content_acquisition.angles.angle_runner._run_angle_llm_on_batch", _run)

    with pytest.raises(ValueError, match="provider configuration failed"):
        analyze_angles_from_texts(["one", "two"], use_cache=False)


def test_analyze_angles_cached_results_stop_and_are_truncated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    texts = ["cached", "must not generate"]
    cache_file = tmp_path / f"{deterministic_hash_sha256(texts[0])}.json"
    cache_file.write_text(
        json.dumps(
            [
                {"source_quote": "q1", "tangent": "t1", "category": "c"},
                {"source_quote": "q2", "tangent": "t2", "category": "c"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner.get_angles_cache_dir", lambda: tmp_path
    )

    def _unexpected(*_args: object, **_kwargs: object) -> list[dict[str, str]]:
        raise AssertionError("generation should not run after cached target is reached")

    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._run_angle_llm_on_batch", _unexpected
    )

    with caplog.at_level("INFO"):
        result = analyze_angles_from_texts(texts, use_cache=True, max_results=1)

    assert [row["source_quote"] for row in result] == ["q1"]
    completion = next(
        record
        for record in caplog.records
        if record.msg == "angles_analyze_from_texts_complete"
    )
    assert completion.processed_blocks == 1
    assert completion.failed_blocks == 0
    assert completion.cached_blocks == 1
    assert completion.generated_blocks == 0


def test_workflow_angles_isolate_malformed_block_and_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def _run(
        _llm: object,
        *,
        batch: list[str],
        **_kwargs: object,
    ) -> list[dict[str, str]]:
        calls.append(batch[0])
        if batch[0] == "bad":
            raise MalformedAnglesResponseError("bad workflow response")
        return [{"source_quote": batch[0], "tangent": "t", "category": "c"}]

    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner.get_angles_cache_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        "content_acquisition.angles.angle_runner._run_workflow_angle_batch", _run
    )

    result = analyze_angles_from_texts_via_workflow_llm(
        ["bad", "good", "unused"],
        use_cache=False,
        llm=object(),  # type: ignore[arg-type]
        max_results=1,
    )

    assert [row["source_quote"] for row in result] == ["good"]
    assert calls == ["bad", "good"]
