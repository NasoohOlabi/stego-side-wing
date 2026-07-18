from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[2] / "scripts" / "score_human_likeness_judgments.py"
    spec = importlib.util.spec_from_file_location("score_human_likeness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_uses_posts_as_independent_units() -> None:
    module = _load()
    common = {"provider": "p", "judge_model": "m", "judge_prompt_sha256": "h"}
    rows = [
        {**common, "post_id": "one", "winner": "A", "winning_method": "our_method"},
        {**common, "post_id": "one", "winner": "B", "winning_method": "our_method"},
        {**common, "post_id": "two", "winner": "A", "winning_method": "zlg"},
        {**common, "post_id": "two", "winner": "tie", "winning_method": None},
    ]
    result = module.summarize(rows)
    assert result["independent_post_clusters"] == 2
    assert result["our_method_score"] == 0.625
    assert result["post_cluster_wins"] == 1
    assert result["post_cluster_losses"] == 1


def test_summary_excludes_invalid_results_and_is_deterministic() -> None:
    module = _load()
    rows = [{"post_id": "one", "winner": None, "winning_method": None}]
    assert module.summarize(rows) == module.summarize(rows)
    assert module.summarize(rows)["invalid_judgments"] == 1
    assert module.summarize(rows)["our_method_score"] is None
