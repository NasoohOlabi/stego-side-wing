"""Tests for workflow LLM backend env resolution."""

import pytest

from infrastructure.config import (
    DEFAULT_GOOGLE_AI_STUDIO_MODEL,
    DEFAULT_WORKFLOW_LM_STUDIO_MODEL,
    get_google_ai_studio_model,
    get_google_generative_language_api_key,
    get_google_generative_language_api_keys,
    get_workflow_llm_backend,
    get_workflow_lm_studio_model,
    get_workflow_stego_default_max_retries,
    get_workflow_stego_sample_angle_count,
    resolve_workflow_llm_provider_and_model,
)


def test_get_workflow_llm_backend_default(clear_llm_backend_env: None) -> None:
    assert get_workflow_llm_backend() == "lm_studio"


@pytest.mark.parametrize(
    "value",
    ("google", "GEMINI", "ai_studio"),
)
def test_get_workflow_llm_backend_google_aliases(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", value)
    assert get_workflow_llm_backend() == "google"


def test_get_workflow_llm_backend_lm_studio_string(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    assert get_workflow_llm_backend() == "lm_studio"


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


def test_workflow_lm_studio_model_default(clear_llm_backend_env: None) -> None:
    assert get_workflow_lm_studio_model() == DEFAULT_WORKFLOW_LM_STUDIO_MODEL


def test_balanced_profile_sacrifices_capacity_for_recoverability(
    clear_workflow_capacity_env: None,
) -> None:
    assert get_workflow_stego_sample_angle_count() == 1
    assert get_workflow_stego_default_max_retries() == 6


def test_workflow_lm_studio_model_override(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.setenv("WORKFLOW_LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    assert get_workflow_lm_studio_model("openai/gpt-oss-20b") == "qwen/qwen3.5-9b"


def test_resolve_lm_studio_path_uses_workflow_model_override(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    monkeypatch.setenv("WORKFLOW_LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    assert resolve_workflow_llm_provider_and_model("openai/gpt-oss-20b") == (
        "lm_studio",
        "qwen/qwen3.5-9b",
    )


def test_angles_model_name_uses_workflow_lm_studio_model(
    monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None
) -> None:
    from workflows.utils.angles_llm_config import angles_model_name

    monkeypatch.delenv("ANGLES_MODEL", raising=False)
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.setenv("WORKFLOW_LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    assert angles_model_name() == "qwen/qwen3.5-9b"


def test_resolve_defaults_to_qwen_lm_studio_when_backend_unset(
    clear_llm_backend_env: None,
) -> None:
    assert resolve_workflow_llm_provider_and_model(DEFAULT_WORKFLOW_LM_STUDIO_MODEL) == (
        "lm_studio",
        DEFAULT_WORKFLOW_LM_STUDIO_MODEL,
    )


def test_resolve_google_path(monkeypatch: pytest.MonkeyPatch, clear_llm_backend_env: None) -> None:
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
    """Backend follows ``WORKFLOW_LLM_BACKEND`` changes in ``os.environ``."""
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "google")
    assert get_workflow_llm_backend() == "google"
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "lm_studio")
    assert get_workflow_llm_backend() == "lm_studio"
