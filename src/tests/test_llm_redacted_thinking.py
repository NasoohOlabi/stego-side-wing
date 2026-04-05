"""Tests for stripping <redacted_thinking> from LLM assistant text."""

import json
from pathlib import Path

import pytest

from infrastructure.config import REPO_ROOT
from workflows.adapters.llm import _split_thinking_and_answer, _strip_redacted_thinking

_OPTIONAL_STEGO_LOG = (
    REPO_ROOT / "logs" / "stego_prompts_20260403_064546.log"
)


def test_strip_removes_single_block_and_preserves_answer() -> None:
    raw = (
        "<redacted_thinking>\nstep 1\n</redacted_thinking>\n\nidx: 3\n"
    )
    assert _strip_redacted_thinking(raw) == "idx: 3"


def test_strip_removes_multiple_blocks() -> None:
    raw = (
        "<redacted_thinking>a</redacted_thinking>"
        "after first"
        "<redacted_thinking>b\nc</redacted_thinking>"
        " tail"
    )
    assert _strip_redacted_thinking(raw) == "after first tail"


def test_strip_case_insensitive_tag() -> None:
    raw = "<Redacted_Thinking>x</redacted_thinking>\nidx: 0"
    assert _strip_redacted_thinking(raw) == "idx: 0"


def test_strip_short_think_tags() -> None:
    t = "think"
    raw = f"<{t}>short</{t}>\nidx: 2"
    assert _strip_redacted_thinking(raw) == "idx: 2"


def test_strip_mixed_open_long_close_short() -> None:
    r, t = "redacted_thinking", "think"
    raw = f"<{r}>body</{t}>\nidx: 1"
    assert _strip_redacted_thinking(raw) == "idx: 1"


def test_strip_orphan_close_tag() -> None:
    assert _strip_redacted_thinking("idx: 0\n</think>") == "idx: 0"


def test_strip_empty_when_only_thinking() -> None:
    tag = "redacted_thinking"
    block = f"<{tag}>only</{tag}>"
    assert _strip_redacted_thinking(block) == ""


def test_strip_plain_thinking_process_before_json_array() -> None:
    raw = (
        "Thinking Process:\n\n"
        "1.  **Analyze the Request:**\n"
        "    *   **Role:** Human Redditor.\n\n"
        '["a", "b", "c"]\n'
    )
    assert _strip_redacted_thinking(raw) == '["a", "b", "c"]'


def test_strip_plain_thinking_process_before_json_object() -> None:
    raw = (
        "Thinking Process:\n\n"
        "Step 1.\n"
        '{"texts": ["x", "y", "z"]}\n'
    )
    assert _strip_redacted_thinking(raw) == '{"texts": ["x", "y", "z"]}'


def test_strip_plain_thinking_then_idx_line() -> None:
    raw = (
        "Thinking Process:\n\n"
        "1. compare angles\n"
        "idx: 2\n"
    )
    assert _strip_redacted_thinking(raw) == "idx: 2"


def test_strip_plain_thinking_markdown_bold_header() -> None:
    raw = "**Thinking Process:**\n\nnot json\n\n[1]\n"
    assert _strip_redacted_thinking(raw) == "[1]"


def test_strip_plain_thinking_fenced_json() -> None:
    raw = (
        "Thinking Process:\n\n"
        "```json\n"
        '{"texts": ["u"]}\n'
        "```\n"
    )
    assert _strip_redacted_thinking(raw) == (
        '```json\n{"texts": ["u"]}\n```'
    )


def test_plain_thinking_only_yields_empty() -> None:
    raw = "Thinking Process:\n\n1. still reasoning.\n2. no payload.\n"
    assert _strip_redacted_thinking(raw) == ""


def test_no_strip_when_thinking_not_leading() -> None:
    raw = '["x"]\n\nThinking Process:\nnoise\n'
    out = _strip_redacted_thinking(raw)
    assert "Thinking Process" in out


def test_split_thinking_and_answer_tagged_plus_idx() -> None:
    raw = (
        "<redacted_thinking>\nstep 1\n</redacted_thinking>\n\nidx: 3\n"
    )
    thinking, response = _split_thinking_and_answer(raw)
    assert "redacted_thinking" in thinking
    assert response == "idx: 3"
    assert response == _strip_redacted_thinking(raw)


def test_split_thinking_and_answer_plain_process_plus_json() -> None:
    raw = (
        "Thinking Process:\n\n"
        "1. analyze\n"
        '["a", "b", "c"]\n'
    )
    thinking, response = _split_thinking_and_answer(raw)
    assert "Thinking Process" in thinking
    assert response == '["a", "b", "c"]'


def test_split_thinking_and_answer_thinking_only_empty_response() -> None:
    tag = "redacted_thinking"
    raw = f"<{tag}>only</{tag}>"
    thinking, response = _split_thinking_and_answer(raw)
    assert thinking
    assert response == ""


def test_split_matches_strip_always() -> None:
    samples = [
        "idx: 0",
        "<think>x</think>\nidx: 1",
        "Thinking Process:\n\nidx: 2\n",
    ]
    for raw in samples:
        _, response = _split_thinking_and_answer(raw)
        assert response == _strip_redacted_thinking(raw)


@pytest.mark.skipif(
    not _OPTIONAL_STEGO_LOG.is_file(),
    reason="Optional local capture under logs/ (often gitignored).",
)
def test_strip_real_stego_prompt_log_first_record() -> None:
    """Regression: qwen3.5 emitted long 'Thinking Process:' then JSON array."""
    first = _OPTIONAL_STEGO_LOG.read_text(encoding="utf-8").splitlines()[0]
    raw = json.loads(first)["assistant_response_raw"]
    out = _strip_redacted_thinking(raw)
    assert not out.lstrip().startswith("Thinking")
    assert out.strip().startswith("[")
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 3
    assert all(isinstance(x, str) and x.strip() for x in parsed)
