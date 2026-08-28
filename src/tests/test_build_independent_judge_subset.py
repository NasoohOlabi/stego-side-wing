"""Tests for the independent judge-subset selector."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[2] / "scripts" / "build_independent_judge_subset.py"
    spec = importlib.util.spec_from_file_location("judge_subset", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_select_rows_uses_one_post_and_unique_zlg_text() -> None:
    module = _module()
    rows = [
        {"pair_id": "1", "method": "our_method", "post_id": "post-a"},
        {"pair_id": "1", "method": "zlg", "stegotext": "duplicate"},
        {"pair_id": "2", "method": "our_method", "post_id": "post-a"},
        {"pair_id": "2", "method": "zlg", "stegotext": "unique-a"},
        {"pair_id": "3", "method": "our_method", "post_id": "post-b"},
        {"pair_id": "3", "method": "zlg", "stegotext": "unique-b"},
        {"pair_id": "4", "method": "our_method", "post_id": "post-c"},
        {"pair_id": "4", "method": "zlg", "stegotext": "duplicate"},
    ]
    selected, counts = module.select_rows(rows)
    assert {row["pair_id"] for row in selected} == {"2", "3"}
    assert counts == {
        "source_pairs": 4,
        "source_posts": 3,
        "selected_pairs": 2,
        "selected_posts": 2,
    }


def test_dataset_sources_uses_the_source_run_dataset() -> None:
    module = _module()
    rows = [
        {
            "post_id": "post-a",
            "source_output_file": "C:/campaign/balanced/output-results/post-a.json",
        }
    ]
    assert module.dataset_sources(rows) == {
        "post-a": Path("C:/campaign/balanced/dataset/post-a.json")
    }
