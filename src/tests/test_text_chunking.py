import pytest

from workflows.utils.text_utils import chunk_text_equal_overlap


def test_chunk_single_returns_full():
    assert chunk_text_equal_overlap("hello", 1, 0) == ["hello"]
    assert chunk_text_equal_overlap("", 3, 100) == []


def test_chunk_three_overlap_covers_endpoints():
    text = "a" * 100
    parts = chunk_text_equal_overlap(text, 3, 10)
    assert len(parts) >= 1
    assert parts[0][0] == "a"
    assert parts[-1][-1] == "a"


def test_chunk_invalid_args():
    with pytest.raises(ValueError):
        chunk_text_equal_overlap("x", 0, 0)
    with pytest.raises(ValueError):
        chunk_text_equal_overlap("x", 1, -1)


def test_chunk_no_trim_preserves_whitespace():
    text = "  spaced  "
    parts = chunk_text_equal_overlap(text, 2, 1)
    assert parts[0].startswith("  ")
    assert parts[-1].endswith("  ")
