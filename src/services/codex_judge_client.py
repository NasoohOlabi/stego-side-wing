"""Small, auditable subprocess adapter for offline Codex evaluation calls."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, validate_call


class CodexJudgeConfig(BaseModel):
    """Configuration kept with every offline Codex judgment."""

    model_config = ConfigDict(frozen=True)
    model: str = "gpt-5.6-luna"
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


def _command(
    schema_path: Path, output_path: Path, working_dir: Path, config: CodexJudgeConfig
) -> list[str]:
    command = [
        "codex.cmd",
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


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _usage(stdout: str) -> dict[str, Any] | None:
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


@validate_call
def run_codex_judge(prompt: str, schema_path: Path, config: CodexJudgeConfig) -> CodexJudgeResult:
    """Run Codex with stdin prompt, retrying cacheable malformed/failed calls once."""
    log = logger.bind(component="codex_judge_client")
    last = CodexJudgeResult(error="judge did not run")
    for attempt in range(1, config.max_attempts + 1):
        with tempfile.TemporaryDirectory(prefix="codex_judge_") as directory:
            output = Path(directory) / "final.json"
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    _command(schema_path, output, Path(directory), config),
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
                last = CodexJudgeResult(
                    text=text,
                    parsed=parsed,
                    exit_code=completed.returncode,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    usage=_usage(completed.stdout or ""),
                    attempts=attempt,
                    error=error,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                last = CodexJudgeResult(
                    duration_ms=round((time.monotonic() - started) * 1000),
                    attempts=attempt,
                    error=str(exc),
                )
        if last.error is None:
            return last
        log.warning("codex_judge_attempt_failed", attempt=attempt, error=last.error)
    return last
