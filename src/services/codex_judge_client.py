"""Subprocess adapters for offline Codex / Claude evaluation calls."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, validate_call

JudgeBackend = Literal["claude", "codex"]
DEFAULT_MODELS: dict[JudgeBackend, str] = {
    "claude": "haiku",
    "codex": "gpt-5.6-luna",
}


class CodexJudgeConfig(BaseModel):
    """Configuration kept with every offline LLM judgment."""

    model_config = ConfigDict(frozen=True)
    backend: JudgeBackend = "claude"
    model: str = "haiku"
    reasoning_effort: str = "high"
    timeout_s: int = Field(default=600, gt=0)
    max_attempts: int = Field(default=2, ge=1)
    ignore_user_config: bool = False


class CodexJudgeResult(BaseModel):
    """The final message and machine-readable execution provenance."""

    text: str = ""
    parsed: dict[str, Any] | None = None
    exit_code: int | None = None
    duration_ms: int = 0
    usage: dict[str, Any] | None = None
    codex_version: str | None = None
    attempts: int = 0
    error: str | None = None


def default_model_for_backend(backend: JudgeBackend) -> str:
    return DEFAULT_MODELS[backend]


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _usage_from_jsonl(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        item = _json_object(line)
        if item and isinstance(item.get("usage"), dict):
            return item["usage"]
    return None


def _schema_valid(parsed: dict[str, Any] | None, schema_path: Path) -> bool:
    schema = _json_object(schema_path.read_text(encoding="utf-8")) or {}
    if parsed is None or any(key not in parsed for key in schema.get("required", [])):
        return False
    properties = schema.get("properties", {})
    for key, specification in properties.items():
        value = parsed.get(key)
        if value is None:
            continue
        if specification.get("type") == "integer" and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            return False
        if isinstance(value, int) and not specification.get(
            "minimum", value
        ) <= value <= specification.get("maximum", value):
            return False
    return True


def _codex_bin() -> str:
    return "codex.cmd" if shutil.which("codex.cmd") else "codex"


def _claude_bin() -> str:
    for name in ("claude", "claude.exe"):
        if shutil.which(name):
            return name
    return "claude"


def _codex_command(
    schema_path: Path, output_path: Path, working_dir: Path, config: CodexJudgeConfig
) -> list[str]:
    command = [
        _codex_bin(),
        "exec",
        "--model",
        config.model,
        "-c",
        f"model_reasoning_effort={config.reasoning_effort}",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-C",
        str(working_dir),
    ]
    if config.ignore_user_config:
        command.append("--ignore-user-config")
    return [*command, "-"]


def _claude_command(schema_path: Path, config: CodexJudgeConfig) -> list[str]:
    schema = schema_path.read_text(encoding="utf-8")
    return [
        _claude_bin(),
        "-p",
        "--model",
        config.model,
        "--effort",
        config.reasoning_effort,
        "--output-format",
        "json",
        "--json-schema",
        schema,
    ]


def _parse_claude_payload(stdout: str) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    payload = _json_object(stdout.strip())
    if payload is None:
        return stdout.strip(), None, None
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        return json.dumps(structured, ensure_ascii=False), structured, payload.get("usage")
    result_text = payload.get("result")
    if isinstance(result_text, str):
        return result_text.strip(), _json_object(result_text), payload.get("usage")
    return stdout.strip(), None, payload.get("usage")


def _attempt_codex(
    prompt: str, schema_path: Path, config: CodexJudgeConfig, attempt: int
) -> CodexJudgeResult:
    with tempfile.TemporaryDirectory(
        prefix="codex_judge_", ignore_cleanup_errors=True
    ) as directory:
        output = Path(directory) / "final.json"
        started = time.monotonic()
        try:
            completed = subprocess.run(
                _codex_command(schema_path, output, Path(directory), config),
                input=prompt,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.timeout_s,
                check=False,
            )
            text = output.read_text(encoding="utf-8").strip() if output.exists() else ""
            parsed = _json_object(text)
            error = (
                None
                if completed.returncode == 0 and _schema_valid(parsed, schema_path)
                else "invalid codex output"
            )
            return CodexJudgeResult(
                text=text,
                parsed=parsed,
                exit_code=completed.returncode,
                duration_ms=round((time.monotonic() - started) * 1000),
                usage=_usage_from_jsonl(completed.stdout or ""),
                attempts=attempt,
                error=error,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CodexJudgeResult(
                duration_ms=round((time.monotonic() - started) * 1000),
                attempts=attempt,
                error=str(exc),
            )


def _attempt_claude(
    prompt: str, schema_path: Path, config: CodexJudgeConfig, attempt: int
) -> CodexJudgeResult:
    # ignore_cleanup_errors: on Windows Claude can still hold the cwd briefly
    # after exit; cleanup then raises WinError 32/5 and kills ThreadPool workers.
    with tempfile.TemporaryDirectory(
        prefix="claude_judge_", ignore_cleanup_errors=True
    ) as directory:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                _claude_command(schema_path, config),
                input=prompt,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.timeout_s,
                cwd=directory,
                check=False,
            )
            text, parsed, usage = _parse_claude_payload(completed.stdout or "")
            ok = (
                completed.returncode == 0
                and not (_json_object(completed.stdout or "") or {}).get("is_error")
                and _schema_valid(parsed, schema_path)
            )
            return CodexJudgeResult(
                text=text,
                parsed=parsed,
                exit_code=completed.returncode,
                duration_ms=round((time.monotonic() - started) * 1000),
                usage=usage if isinstance(usage, dict) else None,
                attempts=attempt,
                error=None if ok else "invalid claude output",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CodexJudgeResult(
                duration_ms=round((time.monotonic() - started) * 1000),
                attempts=attempt,
                error=str(exc),
            )


@validate_call
def run_codex_judge(prompt: str, schema_path: Path, config: CodexJudgeConfig) -> CodexJudgeResult:
    """Run the configured judge backend with schema validation and one retry."""
    log = logger.bind(component="codex_judge_client", backend=config.backend)
    runner = _attempt_claude if config.backend == "claude" else _attempt_codex
    last = CodexJudgeResult(error="judge did not run")
    for attempt in range(1, config.max_attempts + 1):
        last = runner(prompt, schema_path, config, attempt)
        if last.error is None:
            return last
        log.warning("judge_attempt_failed", attempt=attempt, error=last.error)
    return last
