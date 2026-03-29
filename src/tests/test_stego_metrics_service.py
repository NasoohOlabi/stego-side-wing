import os
from collections import Counter
from pathlib import Path

from services.stego_metrics_service import js_divergence, kl_divergence, list_metrics_history


def test_kl_divergence_identical_smoothed_near_zero() -> None:
    c = Counter({"a": 2, "b": 1})
    assert kl_divergence(c, c, alpha=1e-6) < 1e-9


def test_js_divergence_identical_near_zero() -> None:
    c = Counter({"x": 3, "y": 1})
    assert js_divergence(c, c, alpha=1e-6) < 1e-9


def test_list_metrics_history_newest_first(tmp_path: Path) -> None:
    m = tmp_path / "metrics"
    m.mkdir()
    first = m / "perplexity_metrics_20200101T000000Z.json"
    second = m / "divergence_metrics_20200202T000000Z.json"
    first.write_text("{}")
    second.write_text("{}")
    os.utime(first, (1, 1))
    os.utime(second, (999_999_999, 999_999_999))
    rows = list_metrics_history(m, kind_filter="all", limit=10, repo_root=tmp_path)
    assert len(rows) == 2
    assert rows[0]["filename"] == second.name
    assert rows[1]["filename"] == first.name
    assert rows[0]["kind"] == "divergence"


def test_list_metrics_history_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert list_metrics_history(missing, limit=5, repo_root=tmp_path) == []
