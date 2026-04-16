"""Shared pytest fixtures for the stego-side-wing test suite."""

from __future__ import annotations

import dotenv
import pytest

import infrastructure.config as infra_config


@pytest.fixture
def clear_llm_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip workflow LLM env keys so defaults are deterministic (no local .env bleed)."""
    strip_keys = (
        "WORKFLOW_LLM_BACKEND",
        "GOOGLE_AI_STUDIO_MODEL",
        "GOOGLE_PALM_API_KEY",
        "GOOGLE_AI_API_KEYS",
        "GOOGLE_AI_API_KEY",
    )
    for key in strip_keys:
        monkeypatch.delenv(key, raising=False)
    loaded: dict[str, str | None] = {}
    if infra_config.ENV_FILE_PATH.exists():
        loaded = dict(dotenv.dotenv_values(str(infra_config.ENV_FILE_PATH)))
    for key in strip_keys:
        loaded.pop(key, None)
    monkeypatch.setattr(infra_config, "_dotenv_values_cache", loaded)
