"""Characterization of ``StegoPipeline.encode`` with its real internals running.

``test_pipeline_stego.py`` stubs out ``_augment_post``, ``_build_samples``,
``_generate_stego_texts`` and ``_evaluate_candidate_groups`` -- which is most of what
``encode`` actually does. That is fine for the branch checks it makes, but it means the
encode body itself is effectively uncovered, so a refactor that moves code between those
stages would not be caught.

These tests fake only the three genuine edges (LLM, backend, decode) and let everything in
between run for real, then assert on the whole returned artifact. They exist to protect the
``encode`` / ``encode_binary_selection_bits`` deduplication.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest

from workflows.pipelines.stego import StegoPipeline

# Grounded in POST angles/context so encode can succeed without any post-generation
# synthetic-anchor fallback (removed; see 2026-08-02 synthetic-anchor incident).
LLM_TEXTS = [
    "The 7-2 council margin on the transit line still feels narrow but overdue.",
    "Start next spring is ambitious for the tunnelling section they still have to fund.",
    "That bus route down 5th has been overloaded for years, so the transit vote matters.",
]

POST: dict[str, Any] = {
    "id": "charac-post-1",
    "title": "City council approves the new transit line",
    "selftext": "The vote was 7-2. Construction is expected to start next spring.",
    "url": "https://example.com/transit",
    "author": "op_user",
    "comments": [
        {
            "id": "c1",
            "author": "alice",
            "body": "Finally. The bus route down 5th has been overloaded for years.",
            "replies": [],
        },
        {
            "id": "c2",
            "author": "carol",
            "body": "Curious how they are funding the tunnelling section.",
            "replies": [],
        },
    ],
    "angles": [
        {"source_quote": "vote was 7-2", "tangent": "council margin", "category": "politics"},
        {
            "source_quote": "start next spring",
            "tangent": "construction timeline",
            "category": "infrastructure",
        },
    ],
    "search_results": [
        {
            "title": "Transit expansion plans",
            "link": "https://example.com/a",
            "snippet": "The council margin was narrow.",
        }
    ],
}


class _FakeLLM:
    """Returns the JSON contract encode expects: exactly three non-empty strings."""

    def __init__(self) -> None:
        self.last_call_metadata: dict[str, Any] = {"elapsed_ms": 1}
        self.prompts: list[str] = []

    def call_llm(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        return json.dumps(LLM_TEXTS)


class _FakeBackend:
    def needle_finder_batch(self, needles: list[Any], haystack: list[Any]) -> dict[str, Any]:
        return {"results": [{"best_match": "The council margin was narrow."} for _ in needles]}


class _AlwaysSelectedDecode:
    """Decodes every candidate to whichever angle the sender selected."""

    def __init__(self) -> None:
        self.selected_idx = 0
        self.calls = 0

    def decode(self, **kwargs: Any) -> int:
        self.calls += 1
        return self.selected_idx


class _NeverMatchingDecode:
    def decode(self, **kwargs: Any) -> None:
        return None


def _build_pipeline(decode: Any, llm: Any | None = None) -> StegoPipeline:
    return StegoPipeline(
        backend=_FakeBackend(),  # pyright: ignore[reportArgumentType]
        llm=llm or _FakeLLM(),  # pyright: ignore[reportArgumentType]
        decode_pipeline=decode,
    )


# Keys every successful encode returns, whichever entry point produced it.
SUCCESS_KEYS = {
    "stego_text",
    "post",
    "selected_angle",
    "angle_index",
    "succeeded",
    "retry_count",
    "tag",
    "sender_audit",
    "embedding",
    "decoded_indices",
    "encoded_samples",
    "validation_details",
}


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_succeeds_and_returns_the_expected_artifact_shape() -> None:
    decode = _AlwaysSelectedDecode()
    pipeline = _build_pipeline(decode)
    # Point the fake decoder at whatever angle augmentation actually selects.
    augmentation = pipeline._augment_post("x", POST)
    decode.selected_idx = int(augmentation["angleEmbedding"]["selectedAngle"]["idx"])

    result = pipeline.encode(payload="meet at noon", post=POST, tag="charac")

    assert result["succeeded"] is True
    assert result["tag"] == "charac"
    assert result["angle_index"] == decode.selected_idx
    assert result["retry_count"] == 0
    assert isinstance(result["stego_text"], str) and result["stego_text"].strip()

    # The keys downstream consumers and saved artifacts depend on.
    assert set(result) >= SUCCESS_KEYS

    audit = result["sender_audit"]
    assert audit["payload_carrier"] == "selection_channel"
    assert audit["raw_payload_bytes"] == len(b"meet at noon")
    assert isinstance(audit["llm_timings"], list) and audit["llm_timings"]
    assert audit["llm_timings"][0]["ok"] is True

    # Real generation ran: the LLM produced a prompt and the decoder was consulted.
    assert decode.calls > 0

    # Whatever text ships must not carry an invisible payload (AGENTS.md carrier rule).
    assert not (set(result["stego_text"]) & {"‌", "‍", "⁠", "⁣"})


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_never_substitutes_a_synthetic_anchor_reply() -> None:
    """Regression: post-generation code must not manufacture a carrier reply.

    Generic model candidates that lack angle context must not be rewritten into a
    templated ``I can see why people keep coming back to …`` sentence. Decode/context
    failure is a legitimate encode failure (see synthetic-anchor incident report).
    """

    class _DisconnectedLLM:
        last_call_metadata: ClassVar[dict[str, Any]] = {"elapsed_ms": 1}

        def call_llm(self, prompt: str, **kwargs: Any) -> str:
            if prompt.startswith("Revise the draft reply"):
                return json.dumps({"text": ""})
            return json.dumps(
                ["Totally unrelated alpha.", "Totally unrelated beta.", "Totally unrelated gamma."]
            )

    decode = _AlwaysSelectedDecode()
    pipeline = _build_pipeline(decode, llm=_DisconnectedLLM())
    augmentation = pipeline._augment_post("x", POST)
    decode.selected_idx = int(augmentation["angleEmbedding"]["selectedAngle"]["idx"])

    result = pipeline.encode(payload="meet at noon", post=POST, tag="charac", max_retries=0)

    blob = json.dumps(result).casefold()
    assert "i can see why people keep coming back to" not in blob
    if result["succeeded"]:
        assert result["stego_text"] in {
            "Totally unrelated alpha.",
            "Totally unrelated beta.",
            "Totally unrelated gamma.",
        }
    else:
        assert result.get("stego_text", "") == ""


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_reports_failure_when_no_candidate_decodes_back() -> None:
    pipeline = _build_pipeline(_NeverMatchingDecode())

    result = pipeline.encode(payload="meet at noon", post=POST, tag="charac", max_retries=0)

    assert result["succeeded"] is False
    assert result["tag"] == "charac"
    assert "sender_audit" in result
    assert "embedding" in result


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_returns_an_exception_result_when_generation_raises() -> None:
    """The retry loop's except arm: a generation failure becomes a result, not a raise."""

    class _BadJsonLLM:
        last_call_metadata: ClassVar[dict[str, Any]] = {"elapsed_ms": 1}

        def call_llm(self, prompt: str, **kwargs: Any) -> str:
            return "not json at all"

    pipeline = _build_pipeline(_AlwaysSelectedDecode(), llm=_BadJsonLLM())

    result = pipeline.encode(payload="p", post=POST, tag="boom", max_retries=0)

    assert result["succeeded"] is False
    assert result["stego_text"] == ""
    assert result["tag"] == "boom"
    assert result["error"]
    details = result["error_details"]
    assert details["reason"] == "Unexpected exception during stego encoding."
    assert details["exception_type"] == "RuntimeError"
    assert "embedding" in result


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_returns_early_when_no_samples_are_built() -> None:
    """The no-samples early return, before the retry loop is ever entered.

    ``_build_samples`` is stubbed rather than starved via config, because the configured
    sample count is clamped to at least one.
    """
    decode = _AlwaysSelectedDecode()
    pipeline = _build_pipeline(decode)
    pipeline._build_samples = lambda aug, post: ([], [])  # type: ignore[method-assign]

    result = pipeline.encode(payload="p", post=POST, tag="empty")

    assert result["succeeded"] is False
    assert result["stego_text"] == ""
    assert result["retry_count"] == 0
    assert result["error"] == "No samples generated from angle embedding"
    assert result["error_details"]["reason"].startswith("Angle embedding produced zero")
    # Never reached generation or validation.
    assert decode.calls == 0


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_accepts_a_context_sharpened_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The context-sharpen arm: drafts miss, a revised candidate is accepted instead.

    Reaching it needs the naturalness gate off so drafts survive long enough for
    the revise path to run.
    """
    monkeypatch.setenv("WORKFLOW_NATURALNESS_GATE_ENABLED", "0")

    class _SharpeningLLM:
        """Returns drafts normally, and a distinctive revision when asked to revise."""

        last_call_metadata: ClassVar[dict[str, Any]] = {"elapsed_ms": 1}

        def call_llm(self, prompt: str, **kwargs: Any) -> str:
            if prompt.startswith("Revise the draft reply"):
                return json.dumps({"text": "SHARPENED start next spring reply."})
            return json.dumps(LLM_TEXTS)

    class _OnlyAcceptsSharpened:
        """Drafts decode to a near-miss; only the sharpened text decodes exactly."""

        def __init__(self) -> None:
            self.selected_idx = 0

        def decode(self, **kwargs: Any) -> int:
            text = str(kwargs.get("stego_text", ""))
            if text.startswith("SHARPENED"):
                return self.selected_idx
            # Near miss, but must stay a valid index: an out-of-range value gets
            # canonicalized back and would read as an exact hit.
            return self.selected_idx - 1 if self.selected_idx > 0 else self.selected_idx + 1

    decode = _OnlyAcceptsSharpened()
    pipeline = _build_pipeline(decode, llm=_SharpeningLLM())
    augmentation = pipeline._augment_post("x", POST)
    decode.selected_idx = int(augmentation["angleEmbedding"]["selectedAngle"]["idx"])

    result = pipeline.encode(payload="meet at noon", post=POST, tag="sharp", max_retries=0)

    assert result["succeeded"] is True
    assert result["stego_text"].startswith("SHARPENED")
    assert result["sender_audit"]["candidate_validation"]["acceptance_source"] == "context_sharpen"
    assert set(result) >= SUCCESS_KEYS


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_natural_sharpened_style_is_selectable_and_sharpens_on_the_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``natural_sharpened`` was implemented but unreachable through config.

    ``should_sharpen`` tests for it, but the config parser could never return it, so the
    branch was dead and sharpening only ever ran on the final retry.
    """
    from infrastructure.config import get_workflow_stego_prompt_style

    monkeypatch.setenv("WORKFLOW_STEGO_PROMPT_STYLE", "natural_sharpened")
    assert get_workflow_stego_prompt_style() == "natural_sharpened"

    class _SharpeningLLM:
        last_call_metadata: ClassVar[dict[str, Any]] = {"elapsed_ms": 1}

        def call_llm(self, prompt: str, **kwargs: Any) -> str:
            if prompt.startswith("Revise the draft reply"):
                return json.dumps({"text": "SHARPENED start next spring reply."})
            return json.dumps(LLM_TEXTS)

    class _OnlyAcceptsSharpened:
        def __init__(self) -> None:
            self.selected_idx = 0

        def decode(self, **kwargs: Any) -> int:
            text = str(kwargs.get("stego_text", ""))
            if text.startswith("SHARPENED"):
                return self.selected_idx
            return self.selected_idx - 1 if self.selected_idx > 0 else self.selected_idx + 1

    monkeypatch.setenv("WORKFLOW_NATURALNESS_GATE_ENABLED", "0")
    decode = _OnlyAcceptsSharpened()
    pipeline = _build_pipeline(decode, llm=_SharpeningLLM())
    augmentation = pipeline._augment_post("x", POST)
    decode.selected_idx = int(augmentation["angleEmbedding"]["selectedAngle"]["idx"])

    # max_retries=2 means the last-retry fallback would not fire on attempt 1.
    result = pipeline.encode(payload="meet at noon", post=POST, tag="sharp", max_retries=2)

    assert result["succeeded"] is True
    assert result["retry_count"] == 0, "sharpening should have happened on the first attempt"
    assert result["stego_text"].startswith("SHARPENED")


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_rejects_a_post_without_angles() -> None:
    pipeline = _build_pipeline(_AlwaysSelectedDecode())

    with pytest.raises(ValueError, match="Post must have angles"):
        pipeline.encode(payload="p", post={"id": "x", "angles": []})


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_binary_selection_bits_succeeds_and_echoes_the_bits() -> None:
    decode = _AlwaysSelectedDecode()
    pipeline = _build_pipeline(decode)
    bits = "10"

    from workflows.utils.stego_codec import augment_post_with_selection_bits

    augmentation = augment_post_with_selection_bits(bits, POST)
    decode.selected_idx = int(augmentation["angleEmbedding"]["selectedAngle"]["idx"])

    result = pipeline.encode_binary_selection_bits(bits=bits, post=POST, tag="bits")

    assert result["succeeded"] is True
    assert isinstance(result["stego_text"], str) and result["stego_text"].strip()
    assert result["tag"] == "bits"
    audit = result["sender_audit"]
    assert audit["payload_transform"] == "diagnostic_binary_selection_bits"
    assert audit["binary_selection_bits"] == bits
    assert audit["compression_skipped"] is True
    assert audit["raw_payload_bytes"] == 0


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_encode_binary_selection_bits_validates_the_bit_alphabet() -> None:
    pipeline = _build_pipeline(_AlwaysSelectedDecode())

    with pytest.raises(ValueError, match="only '0' and '1'"):
        pipeline.encode_binary_selection_bits(bits="10x1", post=POST)


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_both_encode_paths_agree_on_their_shared_result_keys() -> None:
    """The two entry points must keep returning the same core contract.

    This is the invariant the deduplication has to preserve: whatever the shared
    generate/evaluate/retry core produces is identical for both callers.
    """
    shared_keys = SUCCESS_KEYS

    decode_a = _AlwaysSelectedDecode()
    pipeline_a = _build_pipeline(decode_a)
    aug_a = pipeline_a._augment_post("x", POST)
    decode_a.selected_idx = int(aug_a["angleEmbedding"]["selectedAngle"]["idx"])
    payload_result = pipeline_a.encode(payload="hello", post=POST, tag="t")

    from workflows.utils.stego_codec import augment_post_with_selection_bits

    decode_b = _AlwaysSelectedDecode()
    pipeline_b = _build_pipeline(decode_b)
    aug_b = augment_post_with_selection_bits("10", POST)
    decode_b.selected_idx = int(aug_b["angleEmbedding"]["selectedAngle"]["idx"])
    bits_result = pipeline_b.encode_binary_selection_bits(bits="10", post=POST, tag="t")

    assert shared_keys <= set(payload_result)
    assert shared_keys <= set(bits_result)
    assert payload_result["succeeded"] is True
    assert bits_result["succeeded"] is True
