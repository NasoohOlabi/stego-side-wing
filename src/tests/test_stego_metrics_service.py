import os
from collections import Counter
from pathlib import Path

from services import stego_metrics_service as sms
from services.stego_metrics_service import (
    extract_stego_text_unified,
    js_divergence,
    kl_divergence,
    list_metrics_history,
    run_single_post_metrics,
)


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


def test_extract_stego_text_unified_list_then_dict() -> None:
    assert extract_stego_text_unified([{"stegoText": "from array"}]) == "from array"
    assert extract_stego_text_unified({"stegoText": "camel"}) == "camel"
    assert extract_stego_text_unified({"stego_text": "snake"}) == "snake"


def test_run_single_post_metrics_one_file(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "output-results"
    ds = tmp_path / "datasets"
    out.mkdir(parents=True)
    ds.mkdir(parents=True)
    (out / "abc_version_1.json").write_text(
        '[{"stegoText": "hello world stego text here"}]',
        encoding="utf-8",
    )
    (ds / "abc.json").write_text(
        '{"comments": [{"body": "hello world"}]}',
        encoding="utf-8",
    )
    (ds / "other.json").write_text(
        '{"comments": [{"body": "global corpus tokens"}]}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sms,
        "_perplexity_one_text",
        lambda *a, **k: (123.4, "cpu", None),
    )
    data = run_single_post_metrics(
        out / "abc_version_1.json",
        ds,
        stride=256,
        device="cpu",
    )
    assert data["post_id"] == "abc"
    assert data["perplexity"] == 123.4
    assert data["resolved_device"] == "cpu"
    assert data["primary_baseline_matched_post"] is not None
    assert data["primary_baseline_matched_post"]["kl_stego_vs_matched_post"] is not None
    assert data["secondary_baseline_global_corpus"] is not None


def test_run_single_post_metrics_skips_perplexity_without_torch(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "o"
    ds = tmp_path / "d"
    out.mkdir()
    ds.mkdir()
    (out / "x_version_9.json").write_text(
        '{"stego_text": "alpha beta gamma delta"}',
        encoding="utf-8",
    )
    (ds / "x.json").write_text('{"comments": [{"body": "alpha beta"}]}', encoding="utf-8")

    monkeypatch.setattr(
        sms,
        "_perplexity_one_text",
        lambda *a, **k: (None, None, "Perplexity skipped: missing transformers/torch (nope)."),
    )
    data = run_single_post_metrics(out / "x_version_9.json", ds)
    assert data["perplexity"] is None
    assert any("Perplexity skipped" in w for w in data["warnings"])
