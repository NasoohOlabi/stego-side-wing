import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import stego_metrics_service as sms
from services.stego_metrics_service import (
    delete_metrics_output_sample,
    extract_stego_text_unified,
    js_divergence,
    kl_divergence,
    list_metrics_history,
    run_single_post_metrics,
)


@pytest.mark.parametrize("seq_len", [2, 30, 511, 512, 600, 1024, 1500, 2600])
def test_perplexity_windows_cover_each_token_exactly_once(seq_len: int) -> None:
    """Every token must be scored once: the old window arithmetic double-counted them."""
    windows = sms.perplexity_windows(seq_len, stride=512, max_length=1024)

    assert sum(target_len for _, _, target_len in windows) == seq_len
    prev_end = 0
    for begin, end, target_len in windows:
        assert begin <= end - target_len, "target span must start at or after the window start"
        assert end - target_len == prev_end, "windows must be contiguous with no overlap"
        assert end - begin <= 1024, "window must not exceed the model context"
        prev_end = end
    assert prev_end == seq_len


def test_perplexity_windows_single_pass_for_short_text() -> None:
    """Short stego comments must stay a single full-context pass."""
    assert sms.perplexity_windows(23, stride=512, max_length=1024) == [(0, 23, 23)]


def test_control_length_window_matches_stego_distribution() -> None:
    from pathlib import Path as _Path

    samples = [
        (_Path(f"s{i}.json"), "p1", Counter({f"w{j}": 1 for j in range(length)}))
        for i, length in enumerate([5, 10, 20, 20, 25, 30, 40])
    ]

    low, high = sms.control_length_window(samples)

    assert low <= 20 <= high
    assert low >= 1 and high >= low


def test_human_control_holds_comment_out_of_its_own_baseline(tmp_path: Path) -> None:
    """A leaked hold-out would score ~0 and make the control useless as a floor."""
    ds = tmp_path / "datasets"
    ds.mkdir()
    (ds / "p1.json").write_text(
        '{"comments": [{"body": "alpha beta gamma delta epsilon"},'
        ' {"body": "zeta eta theta iota kappa"},'
        ' {"body": "lambda mu nu xi omicron"}]}',
        encoding="utf-8",
    )

    stats = sms.evaluate_human_control(ds, {"p1"}, (1, 100), 1e-6, None)

    assert stats.comparisons == 1
    assert stats.avg_kl > 1.0, "held-out comment must not appear in its own baseline"
    assert 0.0 < stats.avg_jsd <= math.log(2)


def test_alpha_sensitivity_grows_as_smoothing_shrinks() -> None:
    from pathlib import Path as _Path

    stego = [(_Path("a.json"), "p1", Counter({"unseen": 3, "shared": 1}))]
    baselines = {"p1": Counter({"shared": 5, "other": 5})}

    ladder = sms.alpha_sensitivity(stego, baselines, grid=(1e-2, 1e-4, 1e-6))
    values = [ladder["0.01"], ladder["0.0001"], ladder["1e-06"]]

    assert all(v is not None for v in values)
    assert values[0] < values[1] < values[2]  # type: ignore[operator]


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


def test_extract_comment_counter_includes_nested_replies(tmp_path: Path) -> None:
    post = tmp_path / "p1.json"
    post.write_text(
        json.dumps(
            {
                "comments": [
                    {
                        "body": "top alpha beta",
                        "replies": [
                            {
                                "body": "reply gamma delta",
                                "replies": [{"body": "nested epsilon"}],
                            },
                            {"body": "[deleted]"},
                            {"body": "  [removed]  "},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    counter = sms.extract_comment_counter(post)

    assert counter["alpha"] == 1
    assert counter["gamma"] == 1
    assert counter["epsilon"] == 1
    assert "deleted" not in counter
    assert "removed" not in counter


def test_load_global_stats_counts_nested_bodies(tmp_path: Path) -> None:
    ds = tmp_path / "dataset"
    ds.mkdir()
    (ds / "p1.json").write_text(
        json.dumps(
            {
                "comments": [
                    {
                        "body": "one two",
                        "replies": [{"body": "three four"}, {"body": "[deleted]"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    total_posts, global_counter, nonempty = sms.load_global_stats(ds, None)

    assert total_posts == 1
    assert nonempty == 2
    assert global_counter["one"] == 1
    assert global_counter["three"] == 1
    assert "deleted" not in global_counter


def test_load_global_stats_reuses_one_dataset_scan(tmp_path: Path, monkeypatch) -> None:
    ds = tmp_path / "dataset"
    ds.mkdir()
    (ds / "p1.json").write_text('{"comments": [{"body": "one two"}]}', encoding="utf-8")
    sms._GLOBAL_STATS_CACHE.pop(ds.resolve(), None)

    _, first, _ = sms.load_global_stats(ds, None)
    monkeypatch.setattr(sms, "_iter_comment_bodies", lambda _: [])
    _, second, _ = sms.load_global_stats(ds, None)

    assert first == second == Counter({"one": 1, "two": 1})


def test_post_comment_counters_walk_replies(tmp_path: Path) -> None:
    post = tmp_path / "p1.json"
    post.write_text(
        json.dumps(
            {
                "comments": [
                    {"body": "parent tokens here", "replies": [{"body": "child tokens here"}]}
                ]
            }
        ),
        encoding="utf-8",
    )

    counters = sms._post_comment_counters(post)

    assert len(counters) == 2
    assert sum(counters[0].values()) > 0
    assert sum(counters[1].values()) > 0


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


def test_run_single_post_metrics_skips_perplexity_without_torch(
    tmp_path: Path, monkeypatch
) -> None:
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


def test_perplexity_reuses_model_and_tokenizer(monkeypatch) -> None:
    loads = {"tokenizer": 0, "model": 0}

    class Tokenizer:
        @classmethod
        def from_pretrained(cls, _: str, **__: object) -> "Tokenizer":
            loads["tokenizer"] += 1
            return cls()

    class Model:
        config = SimpleNamespace(n_positions=1024)

        @classmethod
        def from_pretrained(cls, _: str, **__: object) -> "Model":
            loads["model"] += 1
            return cls()

        def to(self, _: str) -> "Model":
            return self

        def eval(self) -> None:
            return None

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoModelForCausalLM=Model, AutoTokenizer=Tokenizer))
    monkeypatch.setattr(sms, "compute_text_perplexity", lambda *args: 12.0)
    sms._MODEL_TOKENIZERS.pop("cache-test", None)
    sms._PERPLEXITY_MODELS.pop(("cache-test", "cpu"), None)

    assert sms._perplexity_one_text("first", "cache-test", 512, "cpu")[0] == 12.0
    assert sms._perplexity_one_text("second", "cache-test", 512, "cpu")[0] == 12.0
    assert loads == {"tokenizer": 1, "model": 1}


def test_delete_metrics_output_sample_removes_file(tmp_path: Path) -> None:
    out = tmp_path / "output-results"
    out.mkdir()
    sample = out / "abc_version_3.json"
    sample.write_text("[]", encoding="utf-8")

    deleted = delete_metrics_output_sample(out, sample.name)

    assert deleted["deleted"] is True
    assert deleted["filename"] == sample.name
    assert not sample.exists()


def test_delete_metrics_output_sample_missing_file(tmp_path: Path) -> None:
    out = tmp_path / "output-results"
    out.mkdir()

    with pytest.raises(FileNotFoundError):
        delete_metrics_output_sample(out, "missing_version_1.json")
