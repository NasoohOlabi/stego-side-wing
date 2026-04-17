"""Tests for validate-post JSON diff helpers."""

from __future__ import annotations

from workflows.runner_diff_utils import (
    collect_diff_paths,
    collect_mismatch_value_snippets,
)


def test_collect_mismatch_value_snippets_scalar_diff() -> None:
    rows = collect_mismatch_value_snippets({"x": 1}, {"x": 2}, path_limit=5, snippet_len=40)
    assert len(rows) == 1
    assert rows[0]["path"] == "x"
    assert rows[0]["baseline_snippet"] == "1"
    assert rows[0]["rerun_snippet"] == "2"


def test_collect_mismatch_value_snippets_dict_key_only_on_left() -> None:
    rows = collect_mismatch_value_snippets({"a": 1, "b": 2}, {"b": 2}, path_limit=5)
    assert any(r["path"] == "a" and r["rerun_snippet"] == "(absent)" for r in rows)


def test_collect_mismatch_value_snippets_list_element() -> None:
    rows = collect_mismatch_value_snippets({"items": ["a", "b"]}, {"items": ["a", "c"]})
    assert any(r["path"] == "items[1]" for r in rows)
    paths = collect_diff_paths({"items": ["a", "b"]}, {"items": ["a", "c"]})
    assert "items[1]" in paths


def test_collect_mismatch_value_snippets_type_mismatch() -> None:
    rows = collect_mismatch_value_snippets({"k": []}, {"k": {}})
    assert rows[0]["path"] == "k"
    assert "[" in rows[0]["baseline_snippet"] or rows[0]["baseline_snippet"] == "[]"
