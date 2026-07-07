"""Tests for validate-post pure helpers."""

from __future__ import annotations

from workflows.runner_validate_post import (
    validation_failure_summary_for_log,
    validation_outcome_from_report,
)


def test_validation_failure_summary_match() -> None:
    steps = {
        "data_load": {"comparison": "match"},
        "research": {"comparison": "match"},
        "gen_angles": {"comparison": "match"},
    }
    order = ("data_load", "research", "gen_angles")
    outcome, _ = validation_outcome_from_report(valid=True, steps_report=steps, stage_order=order)
    detail = validation_failure_summary_for_log(
        validation_outcome=outcome, steps_report=steps, stage_order=order
    )
    assert detail.model_dump(exclude_none=True) == {}


def test_validation_failure_summary_mismatch() -> None:
    steps = {
        "data_load": {"comparison": "match"},
        "research": {
            "comparison": "mismatch",
            "changed_keys": ["search_results", "meta.x"],
        },
        "gen_angles": {"comparison": "match"},
    }
    order = ("data_load", "research", "gen_angles")
    outcome, _ = validation_outcome_from_report(valid=False, steps_report=steps, stage_order=order)
    detail = validation_failure_summary_for_log(
        validation_outcome=outcome, steps_report=steps, stage_order=order
    )
    dumped = detail.model_dump(exclude_none=True)
    assert dumped["mismatch_stages"] == ["research"]
    assert dumped["sample_changed_keys"] == ["search_results", "meta.x"]
    assert "forensics_note" in dumped
    assert "steps" in dumped["forensics_note"]


def test_validation_failure_summary_rerun_incomplete() -> None:
    steps = {
        "data_load": {"comparison": "match"},
        "research": {"comparison": "rerun_failed", "error": "google timed out"},
        "gen_angles": {
            "comparison": "skipped",
            "error": "Skipped because an upstream stage failed",
        },
    }
    order = ("data_load", "research", "gen_angles")
    outcome, _ = validation_outcome_from_report(valid=False, steps_report=steps, stage_order=order)
    detail = validation_failure_summary_for_log(
        validation_outcome=outcome, steps_report=steps, stage_order=order
    )
    dumped = detail.model_dump(exclude_none=True)
    assert dumped["failed_stage"] == "research"
    assert dumped["error_snippet"] == "google timed out"
    assert dumped["skipped_stages"] == ["gen_angles"]
    assert "forensics_note" in dumped
