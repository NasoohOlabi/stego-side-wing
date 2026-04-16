"""Angle LLM defaults (prompts, temperature, model id) shared by workflows and legacy runner."""

from __future__ import annotations

from pathlib import Path

from infrastructure.config import REPO_ROOT, get_env

_TEMPERATURE = 0


def _angles_prompt_dir() -> Path:
    return (REPO_ROOT / "src" / "content_acquisition" / "angles").resolve()


def _read_utf8(name: str) -> str:
    return (_angles_prompt_dir() / name).read_text(encoding="utf-8")


SYSTEM_PROMPT = _read_utf8("systemPrompt.txt")
USER_PROMPT_TEMPLATE = _read_utf8("userPrompt.txt")
TEMPERATURE = _TEMPERATURE


def angles_model_name() -> str:
    """LLM id for angles chat/completions; ANGLES_MODEL overrides MODEL."""
    explicit = (get_env("ANGLES_MODEL") or "").strip()
    if explicit:
        return explicit
    fallback = (get_env("MODEL") or "").strip()
    if fallback:
        return fallback
    return "openai/gpt-oss-20b"
