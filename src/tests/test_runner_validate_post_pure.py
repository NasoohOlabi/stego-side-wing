"""Unit tests for pure validate-post outcome helper."""

from __future__ import annotations

from workflows.runner_validate_post import validation_outcome_from_report


def test_validation_outcome_protocol_match() -> None:
    steps = {
        "a": {"comparison": "match", "matches": True},
        "b": {"comparison": "match", "matches": True},
    }
    outcome, _ = validation_outcome_from_report(
        valid=True, steps_report=steps, stage_order=("a", "b")
    )
    assert outcome == "protocol_match"


def test_validation_outcome_mismatch() -> None:
    steps = {
        "a": {"comparison": "match", "matches": True},
        "b": {"comparison": "mismatch", "matches": False},
    }
    outcome, _ = validation_outcome_from_report(
        valid=False, steps_report=steps, stage_order=("a", "b")
    )
    assert outcome == "protocol_mismatch"


def test_validation_outcome_rerun_incomplete() -> None:
    steps = {
        "a": {"comparison": "rerun_failed", "matches": None},
    }
    outcome, _ = validation_outcome_from_report(
        valid=False, steps_report=steps, stage_order=("a",)
    )
    assert outcome == "rerun_incomplete"
