"""Unit tests for Codex / Claude offline judge adapters."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from services.codex_judge_client import CodexJudgeConfig, default_model_for_backend, run_codex_judge


def _schema(tmp_path: Path) -> Path:
    path = tmp_path / "schema.json"
    path.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_default_models() -> None:
    assert default_model_for_backend("claude") == "haiku"
    assert default_model_for_backend("codex") == "gpt-5.6-luna"


def test_claude_backend_parses_structured_output(tmp_path: Path, monkeypatch) -> None:
    schema = _schema(tmp_path)
    payload = {
        "is_error": False,
        "result": '{"ok": true}',
        "structured_output": {"ok": True},
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }

    def fake_run(command, **kwargs):
        assert command[0] in {"claude", "claude.exe"}
        assert "--json-schema" in command
        assert kwargs["input"] == "prompt"
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("services.codex_judge_client.subprocess.run", fake_run)
    result = run_codex_judge(
        "prompt",
        schema,
        CodexJudgeConfig(backend="claude", model="haiku", max_attempts=1),
    )
    assert result.error is None
    assert result.parsed == {"ok": True}
    assert result.usage == {"input_tokens": 3, "output_tokens": 2}


def test_codex_backend_still_available(tmp_path: Path, monkeypatch) -> None:
    schema = _schema(tmp_path)
    output_written: dict[str, str] = {}

    def fake_run(command, **kwargs):
        assert command[0] in {"codex", "codex.cmd"}
        assert "--output-schema" in command
        out = Path(command[command.index("--output-last-message") + 1])
        out.write_text('{"ok": true}', encoding="utf-8")
        output_written["path"] = str(out)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("services.codex_judge_client.subprocess.run", fake_run)
    result = run_codex_judge(
        "prompt",
        schema,
        CodexJudgeConfig(backend="codex", model="gpt-5.6-luna", max_attempts=1),
    )
    assert result.error is None
    assert result.parsed == {"ok": True}
    assert output_written["path"]
