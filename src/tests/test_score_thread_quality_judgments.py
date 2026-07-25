import importlib.util
from pathlib import Path


def _summarize(rows):
    path = Path(__file__).resolve().parents[2] / "scripts" / "score_thread_quality_judgments.py"
    spec = importlib.util.spec_from_file_location("score_thread_quality", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.summarize(rows)


def _row(post: str, method: str, score: int | None) -> dict[str, object]:
    return {
        "metric": "thread_relevance",
        "post_id": post,
        "method": method,
        "score": score,
        "provider": "p",
        "judge_model": "m",
        "judge_prompt_sha256": "h",
    }


def test_summary_pairs_and_clusters_by_post() -> None:
    rows = [
        _row("a", "our_method", 5),
        _row("a", "our_method", 1),
        _row("a", "zlg", 2),
        _row("b", "our_method", 2),
        _row("b", "zlg", 4),
        _row("c", "zlg", 5),
        _row("d", "our_method", None),
    ]
    result = _summarize(rows)
    assert result["independent_paired_post_clusters"] == 2
    assert result["post_cluster_wins"] == 1
    assert result["post_cluster_losses"] == 1
    assert result["invalid_judgments"] == 1
    assert result["our_minus_zlg_post_cluster_mean"] == -0.5


def test_summary_rejects_mixed_metrics() -> None:
    rows = [_row("a", "our_method", 4), {**_row("a", "zlg", 4), "metric": "writing_quality"}]
    try:
        _summarize(rows)
    except ValueError as error:
        assert "one metric" in str(error)
    else:
        raise AssertionError("mixed metrics must fail")
