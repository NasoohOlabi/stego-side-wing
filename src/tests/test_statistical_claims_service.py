import json
from pathlib import Path

import pytest

from services.statistical_claims_service import (
    bootstrap_mean_ci,
    collect_variant_evidence,
    evaluate_claims,
    load_summary_artifacts,
    paired_bootstrap_delta_ci,
    wilson_ci,
    write_claims_report,
)


def _entry(post_id: str, *, jsd: float, pure: bool = True, hidden: int = 0) -> dict:
    return {
        "post_id": post_id,
        "sample_metrics": {
            "receiver_success": True,
            "carrier_metrics": {"hidden_payload_bytes": hidden},
            "capacity_metrics": {"bps_selection": 0.1},
            "quality_metrics": {
                "matched_post_jsd": jsd,
                "matched_post_kl": jsd * 2,
                "perplexity": 20.0,
            },
        },
        "receiver_decode": {
            "decoded_angle_index": 0,
            "recovery_meta": {
                "payload_transform": "secure_compact_v2",
                "used_compressed_full": not pure,
                "recovery_source": "pure_selection_channel"
                if pure
                else "audit_assisted_compressed_full",
            },
        },
    }


def _summary(sample_count: int, *, clean: bool = True, hidden: int = 0) -> dict:
    post_ids = [f"p{i:03d}" for i in range(sample_count)]
    return {
        "provenance": {"git_status_clean": clean, "git_commit": "abc123"},
        "selected_post_ids": post_ids,
        "profile_summaries": [
            {
                "profile": "balanced",
                "variant": "balanced",
                "samples_succeeded": sample_count,
                "samples_failed": 0,
                "entries": [_entry(pid, jsd=0.10, hidden=hidden) for pid in post_ids],
                "failures": [],
            },
            {
                "profile": "capacity",
                "variant": "capacity",
                "samples_succeeded": sample_count,
                "samples_failed": 0,
                "entries": [_entry(pid, jsd=0.12, pure=False, hidden=hidden) for pid in post_ids],
                "failures": [],
            },
        ],
    }


def test_ci_math() -> None:
    rate = wilson_ci(95, 100)
    assert 0.88 < rate["lower"] < rate["upper"] <= 1.0
    mean = bootstrap_mean_ci([1.0, 2.0, 3.0], iterations=100)
    assert mean["n"] == 3
    assert mean["lower"] <= mean["mean"] <= mean["upper"]
    delta = paired_bootstrap_delta_ci([1.0, 2.0], [2.0, 4.0], iterations=100)
    assert delta["n"] == 2
    assert delta["mean_delta"] == 1.5


def test_synthetic_10_sample_run_is_inconclusive_and_writes_report(tmp_path: Path) -> None:
    report = write_claims_report(
        [_summary(10)],
        output_md=tmp_path / "CLAIMS_REPORT.md",
        output_json=tmp_path / "CLAIMS_REPORT.json",
    )

    assert {claim["status"] for claim in report["claims"]} == {"inconclusive"}
    assert (tmp_path / "CLAIMS_REPORT.md").is_file()
    assert (tmp_path / "CLAIMS_REPORT.json").is_file()


def test_clean_200_sample_run_supports_core_claims() -> None:
    report = evaluate_claims([_summary(200)])
    by_id = {claim["id"]: claim for claim in report["claims"]}

    assert by_id["receiver_recovery_reliability"]["status"] == "supported"
    assert by_id["no_invisible_payload_carrier"]["status"] == "supported"
    assert by_id["naturalness_stealth"]["status"] == "supported"


def test_dirty_or_unknown_provenance_marks_claims_inconclusive() -> None:
    report = evaluate_claims([_summary(200, clean=False)])

    assert report["gates"]["provenance"]["accepted"] is False
    assert {claim["status"] for claim in report["claims"]} == {"inconclusive"}


def test_missing_optional_summary_and_provenance_fields_are_safe() -> None:
    artifact = _summary(10)
    artifact.pop("provenance")
    for lane in artifact["profile_summaries"]:
        lane.pop("summary_metrics", None)
    report = evaluate_claims([artifact])

    assert report["gates"]["provenance"]["accepted"] is False
    assert all(variant.aggregate_metrics == {} for variant in collect_variant_evidence([artifact]))


def test_same_post_paired_comparison_produces_paired_ci() -> None:
    report = evaluate_claims([_summary(200)])
    comparison = next(c for c in report["claims"] if c["id"] == "variant_comparisons_pareto")

    assert comparison["status"] == "supported"
    assert comparison["metric"]["same_post_gate"]["passed"] is True
    assert comparison["metric"]["paired_deltas"][0]["matched_post_jsd_delta_ci"]["n"] == 200


def test_audit_assisted_recovery_is_not_counted_as_pure_channel() -> None:
    report = evaluate_claims([_summary(200)])
    capacity_variant = next(v for v in report["variants"] if v["variant"] == "capacity")

    assert capacity_variant["receiver"]["pure_selection_recoveries"] == 0
    assert capacity_variant["receiver"]["audit_assisted_recoveries"] == 200


def test_hidden_payload_evidence_is_not_supported_when_clean_and_large() -> None:
    report = evaluate_claims([_summary(200, hidden=1)])
    claim = next(c for c in report["claims"] if c["id"] == "no_invisible_payload_carrier")

    assert claim["status"] == "not_supported"


def test_volatile_receiver_context_drift_is_reported_separately() -> None:
    artifact = _summary(200)
    artifact["profile_summaries"].append(
        {
            "profile": "volatile_receiver",
            "variant": "volatile_receiver",
            "samples_succeeded": 200,
            "samples_failed": 1,
            "entries": [_entry(f"v{i:03d}", jsd=0.1) for i in range(200)],
            "failures": [{"failure_code": "decode_failure", "stage": "context_drift"}],
        }
    )
    report = evaluate_claims([artifact])
    claim = next(
        c for c in report["claims"] if c["id"] == "ephemeral_snapshot_vs_volatile_receiver"
    )
    volatile = next(
        row for row in claim["metric"]["receiver_modes"] if row["variant"] == "volatile_receiver"
    )

    assert volatile["context_drift_failures"] == 1


def test_load_summary_artifacts_reads_progress_provenance(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(json.dumps(_summary(10, clean=False)), encoding="utf-8")
    (run_dir / "progress.json").write_text(
        json.dumps({"git_status_clean": True, "git_commit": "from-progress"}),
        encoding="utf-8",
    )

    artifacts = load_summary_artifacts([run_dir])
    assert artifacts[0]["_progress"]["git_commit"] == "from-progress"


def test_declared_summary_count_cannot_inflate_observed_sample_count() -> None:
    artifact = _summary(2)
    artifact["profile_summaries"][0]["samples_succeeded"] = 999

    variants = collect_variant_evidence([artifact])

    assert variants[0].successful_samples == 2


def test_receiver_decode_object_without_success_is_not_counted() -> None:
    artifact = _summary(1)
    entry = artifact["profile_summaries"][0]["entries"][0]
    entry["sample_metrics"]["receiver_success"] = False
    entry["receiver_decode"] = {"succeeded": False, "error": "decode_failed"}

    variants = collect_variant_evidence([artifact])

    assert variants[0].receiver_successes == 0


def test_mixed_commits_fail_provenance_gate() -> None:
    first = _summary(200)
    second = _summary(200)
    second["provenance"]["git_commit"] = "different"

    report = evaluate_claims([first, second])

    assert report["gates"]["provenance"]["accepted"] is False
    assert report["gates"]["provenance"]["reason"] == "mixed git commits"


def test_repeated_post_metrics_are_aggregated_instead_of_overwritten() -> None:
    artifact = _summary(2)
    lane = artifact["profile_summaries"][0]
    lane["entries"] = [_entry("same", jsd=0.1), _entry("same", jsd=0.3)]

    variant = collect_variant_evidence([artifact])[0]

    assert variant.per_post_metrics["same"]["matched_post_jsd"] == pytest.approx(0.2)
