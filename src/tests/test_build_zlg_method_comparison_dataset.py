from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "build_zlg_method_comparison_dataset.py"
    )
    spec = importlib.util.spec_from_file_location("build_zlg_dataset", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(pair_id: int, post_id: str, method: str, value: float) -> dict:
    return {
        "pair_id": pair_id,
        "post_id": post_id,
        "sample_index": 0,
        "method": method,
        "jsd_matched_post": value,
    }


def test_clustered_statistics_do_not_treat_repeated_rows_as_independent() -> None:
    module = _load_module()
    rows = [
        _row(0, "p1", "our_method", 0.4),
        _row(0, "p1", "zlg", 0.2),
        _row(1, "p1", "our_method", 0.4),
        _row(1, "p1", "zlg", 0.3),
        _row(2, "p2", "our_method", 0.5),
        _row(2, "p2", "zlg", 0.4),
    ]

    clustered = module._clustered_paired_stats(rows)
    row_level = module._row_level_paired_stats(rows)

    assert clustered["paired_n"] == 2
    assert row_level["paired_n"] == 3
    assert clustered["inference_unit"] == "unique_post_id"


def test_clustered_statistics_collapse_sample_indexes_within_post() -> None:
    module = _load_module()
    rows = [
        {**_row(0, "p1", "our_method", 0.4), "sample_index": 0},
        {**_row(0, "p1", "zlg", 0.2), "sample_index": 0},
        {**_row(1, "p1", "our_method", 0.6), "sample_index": 1},
        {**_row(1, "p1", "zlg", 0.3), "sample_index": 1},
    ]

    assert module._clustered_paired_stats(rows)["paired_n"] == 1


def test_lexical_quality_index_rewards_diversity_and_avoids_repetition() -> None:
    module = _load_module()

    varied = module._quality("five distinct words make prose lively")
    repetitive = module._quality("same same same same same same")

    assert varied["lexical_quality_index"] > repetitive["lexical_quality_index"]
    assert varied["lexical_quality_index_version"] == "lexical_quality_v1"
    assert 0.0 <= repetitive["lexical_quality_index"] <= 100.0
    assert module._quality("")["lexical_quality_index"] == 0.0


def test_lexical_quality_index_is_in_clustered_statistics() -> None:
    module = _load_module()
    rows = [
        {**_row(0, "p1", "our_method", 0.4), "lexical_quality_index": 90.0},
        {**_row(0, "p1", "zlg", 0.2), "lexical_quality_index": 60.0},
    ]

    stats = module._clustered_paired_stats(rows)["lexical_quality_index"]

    assert stats["n"] == 1
    assert stats["mean_delta_zlg_minus_our"] == -30.0


def test_diversity_guard_rejects_repeated_our_method_text_per_post() -> None:
    module = _load_module()
    rows = [
        {**_row(0, "p1", "our_method", 0.4), "stegotext": "same text"},
        {**_row(1, "p1", "our_method", 0.5), "stegotext": " same   text "},
    ]

    with pytest.raises(ValueError, match="p1"):
        module._assert_diversity(rows, minimum_ratio=1.0)


def test_diversity_guard_accepts_distinct_texts_and_reports_ratio() -> None:
    module = _load_module()
    rows = [
        {**_row(0, "p1", "our_method", 0.4), "stegotext": "first"},
        {**_row(1, "p1", "our_method", 0.5), "stegotext": "second"},
    ]

    result = module._assert_diversity(rows, minimum_ratio=1.0)

    assert result["passed"] is True
    assert result["posts"][0]["unique_ratio"] == 1.0


def test_zlg_capacity_fields_recover_useful_bits_from_verified_bytes() -> None:
    module = _load_module()

    fields = module._zlg_capacity_fields(
        {"payload_bytes_actual": 8, "encoded_bits": 80, "decode_ok": True}
    )

    assert fields == {
        "payload_bits_encoded": 64,
        "protocol_overhead_bits": 16,
        "total_embedded_bits": 80,
    }


def test_zlg_capacity_fields_preserve_explicit_accounting() -> None:
    module = _load_module()

    fields = module._zlg_capacity_fields(
        {
            "payload_bits_encoded": 64,
            "protocol_overhead_bits": 7,
            "total_embedded_bits": 71,
        }
    )

    assert fields["payload_bits_encoded"] == 64
    assert fields["protocol_overhead_bits"] == 7
    assert fields["total_embedded_bits"] == 71


def _tangent_report(*, search: int, relaxed: bool = False) -> dict:
    return {
        "kept_count": 4,
        "dropped": {"near_duplicate": 1, "capped": 0},
        "relevance": {
            "mean": 0.5,
            "median": 0.5,
            "scores_kept": [0.2, 0.4, 0.6, 0.8],
        },
        "distinctness": {"mean_pairwise_jaccard": 0.25},
        "source_mix_kept": {"post": 1, "comments": 3 - search, "search_results": search},
        "config": {"min_size": 4},
        "config_hash": "cfg-b" if relaxed else "cfg-a",
        "relaxations": [{"max_similarity": 0.75, "kept_count": 4}] if relaxed else [],
    }


def test_tangent_db_quality_summary_clusters_repeated_rows_by_post() -> None:
    module = _load_module()
    rows = [
        {
            **_row(0, "p1", "our_method", 0.4),
            "tangent_db_report": _tangent_report(search=0),
        },
        {
            **_row(1, "p1", "our_method", 0.5),
            "tangent_db_report": _tangent_report(search=0),
        },
        {
            **_row(2, "p2", "our_method", 0.6),
            "tangent_db_report": _tangent_report(search=2, relaxed=True),
        },
    ]

    summary = module._tangent_db_quality_summary(rows)

    assert summary["version"] == "tangent_db_quality_summary_v1"
    assert summary["report_rows"] == 3
    assert summary["unique_posts"] == 2
    assert summary["inference_unit"] == "unique_post_id"
    assert summary["relevance"]["kept_score_mean_by_post"]["mean"] == 0.5
    assert summary["source_composition"]["search_share_by_post"]["mean"] == 0.25
    assert summary["deduplication"]["posts_with_dedup_drops"] == 2
    assert summary["capacity_floor_relaxation"]["posts_relaxed"] == 1
    assert summary["config_hashes"] == ["cfg-a", "cfg-b"]


def test_tangent_db_quality_summary_is_deterministic_and_handles_missing_reports() -> None:
    module = _load_module()
    rows = [
        {**_row(0, "p2", "our_method", 0.4), "tangent_db_report": _tangent_report(search=1)},
        _row(1, "p1", "our_method", 0.5),
        {**_row(2, "p2", "zlg", 0.6), "tangent_db_report": _tangent_report(search=3)},
    ]

    forward = module._tangent_db_quality_summary(rows)
    reverse = module._tangent_db_quality_summary(list(reversed(rows)))

    assert forward == reverse
    assert forward["unique_posts"] == 1
    assert forward["posts"][0]["post_id"] == "p2"
