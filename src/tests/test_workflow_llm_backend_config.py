"""Tests for workflow LLM backend env resolution."""

import dotenv
import pytest

import infrastructure.config as infra_config
from infrastructure.config import (
    DEFAULT_GOOGLE_AI_STUDIO_MODEL,
    get_google_ai_studio_model,
    get_google_generative_language_api_key,
    get_google_generative_language_api_keys,
    get_workflow_llm_backend,
    resolve_workflow_llm_provider_and_model,
)


@pytest.fixture
def clear_llm_backend_env(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_get_workflow_llm_backend_default(clear_llm_backend_env: None) -> None:
    assert get_workflow_llm_backend() == "google"


@pytest.mark.parametrize(
    "value",
    ("google", "GEMINI", "ai_studio"),
)
def test_get_workflow_llm_backend_google_aliases(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", value)
    assert get_workflow_llm_backend() == "google"


def test_get_google_ai_studio_model_default(clear_llm_backend_env: None) -> None:
    assert get_google_ai_studio_model() == DEFAULT_GOOGLE_AI_STUDIO_MODEL


def test_get_google_ai_studio_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_AI_STUDIO_MODEL", "custom-model")
    assert get_google_ai_studio_model() == "custom-model"


def test_resolve_lm_studio_path(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    assert resolve_workflow_llm_provider_and_model("openai/gpt-oss-20b") == (
        "lm_studio",
        "openai/gpt-oss-20b",
    )


def test_resolve_defaults_to_ai_studio_when_backend_unset(
    clear_llm_backend_env: None,
) -> None:
    assert resolve_workflow_llm_provider_and_model("openai/gpt-oss-20b") == (
        "gemini",
        DEFAULT_GOOGLE_AI_STUDIO_MODEL,
    )


def test_resolve_google_path(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "google")
    monkeypatch.setenv("GOOGLE_AI_STUDIO_MODEL", "my-gemma")
    assert resolve_workflow_llm_provider_and_model("ignored-lm-id") == (
        "gemini",
        "my-gemma",
    )


def test_google_api_key_palm_precedence(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.setenv("GOOGLE_PALM_API_KEY", "palm-key")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "ai-key")
    assert get_google_generative_language_api_key() == "palm-key"
    assert get_google_generative_language_api_keys() == ["palm-key", "ai-key"]


def test_google_api_keys_csv_and_dedupe(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.delenv("GOOGLE_PALM_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_AI_API_KEYS", "k2, k3")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "k1, k2")
    assert get_google_generative_language_api_keys() == ["k2", "k3", "k1"]


def test_google_api_key_ai_fallback(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.delenv("GOOGLE_PALM_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "ai-only")
    assert get_google_generative_language_api_key() == "ai-only"


def test_google_api_key_none_when_unset(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.delenv("GOOGLE_PALM_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_AI_API_KEYS", raising=False)
    monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
    assert get_google_generative_language_api_key() is None
    assert get_google_generative_language_api_keys() == []


def test_dotenv_cache_sees_new_env(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    """After mutating os.environ, backend reads current os.environ (first in get_env)."""
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "google")
    assert get_workflow_llm_backend() == "google"
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    assert get_workflow_llm_backend() == "lm_studio"
