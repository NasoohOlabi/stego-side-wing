"""Tests for stripping <redacted_thinking> from LLM assistant text."""

from workflows.adapters.llm import _strip_redacted_thinking


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
