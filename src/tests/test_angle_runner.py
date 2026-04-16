from io import StringIO

import pytest

from pipelines.angles.angle_runner import _emit_status, _parse_or_repair_workflow


class _AsciiOnlyStream(StringIO):
    def write(self, s):
        if any(ord(ch) > 127 for ch in s):
            raise UnicodeEncodeError("ascii", s, 0, 1, "ordinal not in range(128)")
        return super().write(s)


def test_emit_status_falls_back_to_ascii(monkeypatch):
    stream = _AsciiOnlyStream()
    monkeypatch.setattr("pipelines.angles.angle_runner.sys.stdout", stream)

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
