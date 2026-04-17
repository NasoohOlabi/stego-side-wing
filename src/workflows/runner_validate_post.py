"""Pure logic for validate-post outcome classification (keeps runner orchestration thin)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, validate_call


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


_MAX_CHANGED_KEYS = 12
_MAX_ERROR_SNIPPET = 240


class ValidationFailureDetailForLog(BaseModel):
    """Bounded fields for validate-post failure logging (jq-friendly)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mismatch_stages: list[str] | None = None
    sample_changed_keys: list[str] | None = None
    failed_stage: str | None = None
    error_snippet: str | None = Field(default=None, max_length=_MAX_ERROR_SNIPPET)
    skipped_stages: list[str] | None = None


def _truncate_err(raw: object, cap: int) -> str:
    text = raw if isinstance(raw, str) else str(raw)
    return text[:cap] if len(text) > cap else text


def _sample_changed_keys(step: Mapping[str, Any], cap: int) -> list[str]:
    raw = step.get("changed_keys") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if len(out) >= cap:
            break
        out.append(x if isinstance(x, str) else str(x))
    return out


def _mismatch_summary(
    steps_report: Mapping[str, Mapping[str, Any]],
    stage_order: tuple[str, ...],
) -> ValidationFailureDetailForLog:
    mismatched = [s for s in stage_order if steps_report.get(s, {}).get("comparison") == "mismatch"]
    keys: list[str] = []
    for s in mismatched:
        keys = _sample_changed_keys(steps_report.get(s, {}), _MAX_CHANGED_KEYS)
        if keys:
            break
    return ValidationFailureDetailForLog(
        mismatch_stages=mismatched or None,
        sample_changed_keys=keys or None,
    )


def _incomplete_summary(
    steps_report: Mapping[str, Mapping[str, Any]],
    stage_order: tuple[str, ...],
) -> ValidationFailureDetailForLog:
    failed_stage: str | None = None
    err: str | None = None
    skipped: list[str] = []
    seen_failed = False
    for s in stage_order:
        comp = steps_report.get(s, {}).get("comparison")
        if seen_failed:
            if comp == "skipped":
                skipped.append(s)
            continue
        if comp == "rerun_failed":
            failed_stage = s
            err = _truncate_err(steps_report.get(s, {}).get("error", ""), _MAX_ERROR_SNIPPET)
            seen_failed = True
    return ValidationFailureDetailForLog(
        failed_stage=failed_stage,
        error_snippet=err,
        skipped_stages=skipped or None,
    )


@validate_call
def validation_failure_summary_for_log(
    *,
    validation_outcome: str,
    steps_report: Mapping[str, Mapping[str, Any]],
    stage_order: tuple[str, ...],
) -> ValidationFailureDetailForLog:
    """Build a small structured summary for logging (no secrets, capped size)."""
    if validation_outcome == "protocol_match":
        return ValidationFailureDetailForLog()
    if validation_outcome == "protocol_mismatch":
        return _mismatch_summary(steps_report, stage_order)
    return _incomplete_summary(steps_report, stage_order)
