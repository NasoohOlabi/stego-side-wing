"""Shared pytest fixtures for the stego-side-wing test suite."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import dotenv
import pytest

import infrastructure.config as infra_config

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


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
        "WORKFLOW_CODEC_DICTIONARY_LIMITS_ENABLED",
        "WORKFLOW_RESEARCH_MAX_TERMS",
        "WORKFLOW_RESEARCH_MAX_SELECTED_URLS",
        "WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS",
        "WORKFLOW_DICTIONARY_MAX_COMMENTS",
        "WORKFLOW_ANGLES_MAX_INPUT_BLOCKS",
        "WORKFLOW_ANGLES_MAX_OUTPUT",
        "WORKFLOW_ANGLES_RAW_TARGET_MULTIPLIER",
        "WORKFLOW_ANGLES_GENERATION_MODE",
        "WORKFLOW_STEGO_GENERATION_MODE",
        "WORKFLOW_STEGO_PROMPT_STYLE",
        "WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT",
        "WORKFLOW_STEGO_MAX_RETRIES",
        "WORKFLOW_DECODE_SEMANTIC_TOP_N",
        "WORKFLOW_DECODE_LLM_MAX_TRIES",
        "WORKFLOW_STEGO_LLM_TEMPERATURE",
        "WORKFLOW_DECODE_STRICT_DEFAULT",
        "WORKFLOW_NATURALNESS_GATE_ENABLED",
        "WORKFLOW_NATURALNESS_GATE_MODE",
        "WORKFLOW_BARB_STANCE_GATE",
        "WORKFLOW_TANGENT_DB_BUILDER",
        "WORKFLOW_TANGENT_DB_MIN_RELEVANCE",
        "WORKFLOW_TANGENT_DB_SEARCH_RELEVANCE_MULT",
        "WORKFLOW_TANGENT_DB_MAX_SIMILARITY",
        "WORKFLOW_TANGENT_DB_MIN_SIZE",
        "WORKFLOW_TANGENT_DB_SEMANTIC_DEDUP",
    )
    for key in strip_keys:
        monkeypatch.delenv(key, raising=False)
    loaded: dict[str, str | None] = {}
    if infra_config.ENV_FILE_PATH.exists():
        loaded = dict(dotenv.dotenv_values(str(infra_config.ENV_FILE_PATH)))
    for key in strip_keys:
        loaded.pop(key, None)
    monkeypatch.setattr(infra_config, "_dotenv_values_cache", loaded)


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a test reaches a non-loopback host.

    The suite is expected to run offline (CI provides no API keys). Without this guard a
    refactor that accidentally drops a mock becomes a slow, flaky test instead of an
    obvious failure.

    Loopback stays open on purpose: ``BackendAPIAdapter.needle_finder_batch`` posts to the
    local API and falls back to in-process work when the connection is refused, so blocking
    it would change which code path the tests exercise.
    """
    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any, /) -> Any:
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, str) and host not in _LOOPBACK_HOSTS:
            raise OSError(
                f"Blocked outbound connection to {host!r} during tests. "
                f"Mock the client instead of making a real request."
            )
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


class FakeLLMAdapter:
    """Stand-in for ``workflows.adapters.llm.LLMAdapter`` that records its calls.

    ``responses`` is consumed in order; the final entry repeats once exhausted, so a test
    that does not care how many generations happen can pass a single response.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses) if responses else [""]
        self.calls: list[dict[str, Any]] = []
        self.last_call_metadata: dict[str, Any] = {}

    def call_llm(
        self,
        prompt: str,
        system_message: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "system_message": system_message,
                "model": model,
                "provider": provider,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class FakeBackendAdapter:
    """Stand-in for ``workflows.adapters.backend_api.BackendAPIAdapter``.

    Only the methods workflow code actually calls are implemented. ``needle_finder_batch``
    echoes the first haystack entry so ``StegoPipeline._build_samples`` receives a stable
    ``best_match`` without any HTTP.
    """

    def __init__(self, posts: dict[str, dict[str, Any]] | None = None) -> None:
        self.posts = posts or {}
        self.saved_objects: list[dict[str, Any]] = []
        self.saved_posts: list[dict[str, Any]] = []

    def needle_finder_batch(self, needles: list[str], haystack: list[str]) -> dict[str, Any]:
        best = haystack[0] if haystack else ""
        return {"results": [{"best_match": best} for _ in needles]}

    def get_post_local(self, post_filename: str, step: str) -> dict[str, Any]:
        return self.posts.get(post_filename, {})

    def save_post_local(self, post: dict[str, Any], step: str) -> None:
        self.saved_posts.append({"post": post, "step": step})

    def save_object_local(self, data: Any, step: str, filename: str) -> None:
        self.saved_objects.append({"data": data, "step": step, "filename": filename})


@pytest.fixture
def fake_llm() -> Callable[..., FakeLLMAdapter]:
    """Build a :class:`FakeLLMAdapter` with scripted responses."""

    def _build(responses: list[str] | None = None) -> FakeLLMAdapter:
        return FakeLLMAdapter(responses)

    return _build


@pytest.fixture
def fake_backend() -> Callable[..., FakeBackendAdapter]:
    """Build a :class:`FakeBackendAdapter` seeded with optional local posts."""

    def _build(posts: dict[str, dict[str, Any]] | None = None) -> FakeBackendAdapter:
        return FakeBackendAdapter(posts)

    return _build
