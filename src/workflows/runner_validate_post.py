"""Pure logic for validate-post outcome classification (keeps runner orchestration thin)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def validation_outcome_from_report(
    *,
    valid: bool,
    steps_report: Mapping[str, Mapping[str, Any]],
    stage_order: tuple[str, ...],
) -> tuple[str, str]:
    """Return ``(validation_outcome, validation_explanation)`` given overall ``valid`` flag."""
    if valid:
        return (
            "protocol_match",
            "All three stages completed and each live rerun matched its saved artifact.",
        )
    if any(steps_report.get(s, {}).get("comparison") == "mismatch" for s in stage_order):
        return (
            "protocol_mismatch",
            "At least one stage finished rerunning but the live payload differed from the saved "
            "artifact. That is a true baseline-vs-rerun mismatch (see comparison / changed_keys on "
            "those stages).",
        )
    return (
        "rerun_incomplete",
        "A stage failed during rerun or was skipped, so validation could not establish whether "
        "the protocol still matches baselines. This is not labeled as a protocol mismatch; "
        "fix the failing stage and retry.",
    )
