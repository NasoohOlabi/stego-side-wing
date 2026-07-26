"""Pure logic for validate-post outcome classification (keeps runner orchestration thin)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, validate_call

from workflows.runner_diff_utils import collect_diff_paths, collect_mismatch_value_snippets


def _skipped_step(step: str) -> dict[str, Any]:
    return {
        "step": step,
        "comparison": "skipped",
        "matches": None,
        "changed_keys": [],
        "changed_key_snippets": [],
        "comparison_note": (
            "Not compared: a previous stage failed during rerun, so this stage was skipped. "
            "This is not a baseline-vs-rerun mismatch."
        ),
        "error": "Skipped because an upstream stage failed during rerun",
    }


def _failed_step(
    *,
    stage: str,
    step: str,
    error: str,
    baseline: dict[str, Any],
    protocol_report: dict[str, Any] | None,
    summarize: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    return {
        "step": step,
        "comparison": "rerun_failed",
        "matches": None,
        "changed_keys": [],
        "changed_key_snippets": [],
        "comparison_note": (
            "Live rerun did not finish successfully, so the saved artifact was not compared "
            "to a fresh rerun. Treat this as an execution/network/provider failure, not a "
            "protocol drift mismatch."
        ),
        "error": error,
        "baseline_summary": summarize(stage, baseline),
        "protocol_report": protocol_report,
    }


def _compared_step(
    *,
    stage: str,
    step: str,
    baseline: dict[str, Any],
    rerun: dict[str, Any],
    protocol_report: dict[str, Any] | None,
    summarize: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    matches = baseline == rerun
    return (
        {
            "step": step,
            "comparison": "match" if matches else "mismatch",
            "matches": matches,
            "changed_keys": [] if matches else collect_diff_paths(baseline, rerun),
            "changed_key_snippets": (
                [] if matches else collect_mismatch_value_snippets(baseline, rerun)
            ),
            "comparison_note": (
                "Saved artifact and live rerun are byte-for-byte equal."
                if matches
                else (
                    "Mismatch: live rerun produced different JSON than the saved workflow artifact "
                    "for this stage (see changed_keys and changed_key_snippets). This indicates "
                    "protocol or data drift, not a failed rerun."
                )
            ),
            "baseline_summary": summarize(stage, baseline),
            "rerun_summary": summarize(stage, rerun),
            "protocol_report": protocol_report,
        },
        matches,
    )


def build_steps_report(
    *,
    stage_steps: Mapping[str, str],
    baseline: dict[str, dict[str, Any]],
    rerun_payloads: dict[str, dict[str, Any]],
    stage_errors: dict[str, str],
    protocol_reports: dict[str, dict[str, Any]],
    summarize: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Compare each completed rerun and classify downstream skipped stages."""
    report: dict[str, dict[str, Any]] = {}
    valid = True
    upstream_failed = False
    for stage, step in stage_steps.items():
        if upstream_failed:
            report[stage] = _skipped_step(step)
            valid = False
        elif stage in stage_errors:
            report[stage] = _failed_step(
                stage=stage,
                step=step,
                error=stage_errors[stage],
                baseline=baseline[stage],
                protocol_report=protocol_reports.get(stage),
                summarize=summarize,
            )
            valid = False
            upstream_failed = True
        else:
            report[stage], matches = _compared_step(
                stage=stage,
                step=step,
                baseline=baseline[stage],
                rerun=rerun_payloads[stage],
                protocol_report=protocol_reports.get(stage),
                summarize=summarize,
            )
            valid = valid and matches
    return report, valid


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
            "artifact. That is a true baseline-vs-rerun mismatch (see each stage’s comparison, "
            "changed_keys, and changed_key_snippets in the HTTP/SSE result).",
        )
    return (
        "rerun_incomplete",
        "A stage failed during rerun or was skipped, so validation could not establish whether "
        "the protocol still matches baselines. This is not labeled as a protocol mismatch; "
        "fix the failing stage and retry.",
    )


_MAX_CHANGED_KEYS = 12
_MAX_ERROR_SNIPPET = 240
_MAX_FOREnsics_NOTE = 280

_MISMATCH_FOREnsics_NOTE = (
    "Full diff: use validate-post HTTP/SSE body `steps` (changed_keys, changed_key_snippets, "
    "summaries, protocol_report). JSONL only samples keys."
)
_INCOMPLETE_FOREnsics_NOTE = (
    "Stage errors: use validate-post HTTP/SSE body `steps` (error, protocol_report per stage). "
    "JSONL only lists failed_stage and error_snippet."
)


class ValidationFailureDetailForLog(BaseModel):
    """Bounded fields for validate-post failure logging (jq-friendly)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mismatch_stages: list[str] | None = None
    sample_changed_keys: list[str] | None = None
    failed_stage: str | None = None
    error_snippet: str | None = Field(default=None, max_length=_MAX_ERROR_SNIPPET)
    skipped_stages: list[str] | None = None
    forensics_note: str | None = Field(default=None, max_length=_MAX_FOREnsics_NOTE)


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
        forensics_note=_MISMATCH_FOREnsics_NOTE,
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
        forensics_note=_INCOMPLETE_FOREnsics_NOTE,
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
