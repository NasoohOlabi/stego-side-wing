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
        "WORKFLOW_LM_STUDIO_MODEL",
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


@pytest.fixture
def clear_workflow_capacity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip capacity env keys so tests do not inherit local workflow fan-out settings."""
    strip_keys = (
        "WORKFLOW_CAPACITY_PROFILE",
        "WORKFLOW_ENCODING_PROFILE",
        "WORKFLOW_PAYLOAD_TRANSFORM",
        "WORKFLOW_ENCODING_SECRET",
        "WORKFLOW_CAPACITY_LIMITS_ENABLED",
        "WORKFLOW_RESEARCH_MAX_TERMS",
        "WORKFLOW_RESEARCH_MAX_SELECTED_URLS",
        "WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS",
        "WORKFLOW_DICTIONARY_MAX_COMMENTS",
        "WORKFLOW_ANGLES_MAX_INPUT_BLOCKS",
        "WORKFLOW_ANGLES_MAX_OUTPUT",
        "WORKFLOW_ANGLES_GENERATION_MODE",
        "WORKFLOW_STEGO_GENERATION_MODE",
        "WORKFLOW_STEGO_PROMPT_STYLE",
        "WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT",
        "WORKFLOW_STEGO_MAX_RETRIES",
        "WORKFLOW_DECODE_SEMANTIC_TOP_N",
        "WORKFLOW_DECODE_LLM_MAX_TRIES",
        "WORKFLOW_STEGO_LLM_TEMPERATURE",
        "WORKFLOW_DECODE_STRICT_DEFAULT",
    )
    for key in strip_keys:
        monkeypatch.delenv(key, raising=False)
    loaded: dict[str, str | None] = {}
    if infra_config.ENV_FILE_PATH.exists():
        loaded = dict(dotenv.dotenv_values(str(infra_config.ENV_FILE_PATH)))
    for key in strip_keys:
        loaded.pop(key, None)
    monkeypatch.setattr(infra_config, "_dotenv_values_cache", loaded)
