"""Adaptive feedback helpers for stego e2e runs."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import validate_call

from workflows.utils.protocol_utils import angle_signature as _angle_signature
from workflows.utils.protocol_utils import stable_hash

FAILURE_CODES = {
    "data_load_empty_selftext",
    "research_empty_results",
    "angles_missing_or_low_diversity",
    "stego_invalid_json",
    "stego_contextuality_reject",
    "receiver_context_drift",
    "receiver_angle_mismatch",
    "receiver_payload_recovery_failed",
    "receiver_payload_mismatch",
    "infra_transient",
    "generation_failure",
}

INFRA_ERROR_MARKERS = (
    "404",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "connection",
    "name resolution",
    "permission_denied",
    "api_key",
    "service unavailable",
    "remote end closed connection",
)


@dataclass
class AdaptiveSampleState:
    """Per-sample adaptation state shared across retries."""

    max_padding_bits: int = 256
    stego_max_retries_bonus: int = 0
    env_overrides: dict[str, str] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _safe_len(value: Any) -> int:
    return len(value) if isinstance(value, list | str | dict) else 0


def _flatten_angles(raw: Any) -> list[dict[str, Any]]:
    """Flatten one level of angle nesting, preserving each angle dict as-is.

    Deliberately *not* ``stego_codec.flatten_angle_groups``: that one injects a positional
    ``idx`` key into every angle, which would change what this service reports.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, list):
            out.extend(angle for angle in item if isinstance(angle, dict))
    return out


def summarize_input_post(post: dict[str, Any], baseline_post: dict[str, Any]) -> dict[str, Any]:
    selftext = post.get("selftext")
    search_results = post.get("search_results")
    angles = _flatten_angles(post.get("angles"))
    signatures = [_angle_signature(angle) for angle in angles]
    duplicates = len(signatures) - len(set(signatures))
    return {
        "data_load": {
            "has_url": isinstance(post.get("url"), str) and bool(str(post.get("url")).strip()),
            "has_selftext": isinstance(selftext, str) and bool(selftext.strip()),
            "selftext_length": len(selftext) if isinstance(selftext, str) else 0,
            "selftext_hash": stable_hash(selftext or ""),
            "baseline_has_selftext": isinstance(baseline_post.get("selftext"), str)
            and bool(str(baseline_post.get("selftext")).strip()),
        },
        "research": {
            "search_results_count": _safe_len(search_results),
            "search_results_hash": stable_hash(search_results or []),
            "low_quality": not isinstance(search_results, list) or len(search_results) == 0,
        },
        "gen_angles": {
            "angles_count": len(angles),
            "angles_hash": stable_hash(post.get("angles", [])),
            "duplicate_angle_signatures": duplicates,
            "low_diversity": len(angles) < 3 or duplicates > max(2, len(angles) // 5),
        },
    }


def summarize_stego_result(stego_result: dict[str, Any]) -> dict[str, Any]:
    validation = stego_result.get("validation_details")
    validation_dict = validation if isinstance(validation, dict) else {}
    candidates = validation_dict.get("candidates")
    error_details = stego_result.get("error_details")
    error_dict = error_details if isinstance(error_details, dict) else {}
    return {
        "succeeded": bool(stego_result.get("succeeded")),
        "error": stego_result.get("error"),
        "retry_count": stego_result.get("retry_count"),
        "prompt_style": stego_result.get("prompt_style"),
        "candidate_count": len(candidates) if isinstance(candidates, list) else None,
        "contextuality_reasons": error_dict.get("candidate_results")
        or validation_dict.get("candidates")
        or [],
        "selected_angle_index": stego_result.get("angle_index"),
        "has_stego_text": isinstance(stego_result.get("stego_text"), str)
        and bool(str(stego_result.get("stego_text")).strip()),
    }


def summarize_receiver_decode(receiver_info: dict[str, Any] | None) -> dict[str, Any]:
    info = receiver_info if isinstance(receiver_info, dict) else {}
    return {
        "decoded_angle_index": info.get("decoded_angle_index"),
        "semantic_decoded_angle_index": info.get("semantic_decoded_angle_index"),
        "has_angle_mismatch": (
            isinstance(info.get("semantic_decoded_angle_index"), int)
            and info.get("semantic_decoded_angle_index") != info.get("decoded_angle_index")
        ),
        "recovery_meta": info.get("recovery_meta")
        if isinstance(info.get("recovery_meta"), dict)
        else {},
    }


@validate_call
def classify_failure(
    error_text: str,
    *,
    envelope: dict[str, Any] | None = None,
) -> str:
    normalized = error_text.lower()
    env = envelope or {}
    if any(marker in normalized for marker in INFRA_ERROR_MARKERS):
        return "infra_transient"
    if "valid json" in normalized or ("json" in normalized and "stego llm" in normalized):
        return "stego_invalid_json"
    if "context" in normalized and ("faithful" in normalized or "ground" in normalized):
        return "stego_contextuality_reject"
    if "context_drift" in normalized or "context drift" in normalized:
        return "receiver_context_drift"
    if "angle index" in normalized or "decoded angle" in normalized:
        return "receiver_angle_mismatch"
    if "different payload" in normalized:
        return "receiver_payload_mismatch"
    if "recover payload" in normalized or "compressed bitstring" in normalized:
        return "receiver_payload_recovery_failed"
    if "decoding validation failed" in normalized or "decode" in normalized:
        raw_stego = env.get("stego_encode")
        stego = raw_stego if isinstance(raw_stego, dict) else {}
        reasons = str(stego.get("contextuality_reasons") or "").lower()
        if "context" in reasons or "ground" in reasons:
            return "stego_contextuality_reject"
        return "receiver_angle_mismatch"
    data_load = env.get("data_load") if isinstance(env.get("data_load"), dict) else {}
    if data_load and not data_load.get("has_selftext"):
        return "data_load_empty_selftext"
    research = env.get("research") if isinstance(env.get("research"), dict) else {}
    if research and int(research.get("search_results_count") or 0) == 0:
        return "research_empty_results"
    angles = env.get("gen_angles") if isinstance(env.get("gen_angles"), dict) else {}
    if angles and (int(angles.get("angles_count") or 0) == 0 or angles.get("low_diversity")):
        return "angles_missing_or_low_diversity"
    return "generation_failure"


def plan_adaptive_action(failure_code: str, state: AdaptiveSampleState) -> dict[str, Any] | None:
    """Return the next safe runtime adaptation for a normalized failure."""
    if failure_code == "stego_invalid_json" and state.stego_max_retries_bonus < 2:
        state.stego_max_retries_bonus += 1
        return {"action": "increase_stego_max_retries", "value": state.stego_max_retries_bonus}
    if (
        failure_code == "stego_contextuality_reject"
        and "WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT" not in state.env_overrides
    ):
        state.env_overrides["WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT"] = "6"
        return {"action": "increase_candidate_angles", "env": dict(state.env_overrides)}
    if (
        failure_code == "receiver_angle_mismatch"
        and "WORKFLOW_DECODE_SEMANTIC_TOP_N" not in state.env_overrides
    ):
        state.env_overrides["WORKFLOW_DECODE_SEMANTIC_TOP_N"] = "40"
        state.env_overrides["WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT"] = "6"
        return {"action": "expand_angle_search", "env": dict(state.env_overrides)}
    if failure_code == "receiver_payload_recovery_failed" and state.max_padding_bits < 1024:
        state.max_padding_bits = 1024
        return {
            "action": "expand_payload_recovery_padding",
            "max_padding_bits": state.max_padding_bits,
        }
    if failure_code == "receiver_context_drift":
        return {"action": "record_context_drift_for_cached_audit_review"}
    return None


class StegoFeedbackRun:
    """Writes feedback artifacts for one adaptive e2e run."""

    def __init__(self, run_dir: Path, *, baseline_failure_rate: float = 0.54) -> None:
        self.run_dir = run_dir
        self.baseline_failure_rate = baseline_failure_rate
        self.events_path = run_dir / "events.jsonl"
        self.actions_path = run_dir / "adaptive_actions.jsonl"
        self._events: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        run_dir.mkdir(parents=True, exist_ok=True)

    def record_event(self, event: dict[str, Any]) -> None:
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), **event}
        self._events.append(payload)
        _append_jsonl(self.events_path, payload)

    def record_action(self, action: dict[str, Any]) -> None:
        payload = {"created_at_utc": datetime.now(UTC).isoformat(), **action}
        self._actions.append(payload)
        _append_jsonl(self.actions_path, payload)

    def finalize(self, e2e_summary: dict[str, Any]) -> dict[str, Any]:
        failures = [event for event in self._events if event.get("outcome") == "failed"]
        counter = Counter(
            str(event.get("failure_code") or "generation_failure") for event in failures
        )
        clusters = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "clusters": [
                {
                    "failure_code": code,
                    "count": count,
                    "likely_subsystem": code.split("_", 1)[0],
                    "examples": [event for event in failures if event.get("failure_code") == code][
                        :5
                    ],
                }
                for code, count in counter.most_common()
            ],
        }
        _write_json(self.run_dir / "failure_clusters.json", clusters)

        requested = int(e2e_summary.get("total_requested_samples") or 0)
        failed = int(e2e_summary.get("total_failed_samples") or 0)
        succeeded = int(e2e_summary.get("total_succeeded_samples") or 0)
        failure_rate = failed / requested if requested else None
        rows = []
        for lane in e2e_summary.get("profile_summaries", []):
            if not isinstance(lane, dict):
                continue
            raw_metrics = lane.get("summary_metrics")
            metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
            raw_quality = metrics.get("quality_metrics")
            quality = raw_quality if isinstance(raw_quality, dict) else {}
            lane_requested = int(lane.get("requested_samples") or 0)
            lane_failed = int(lane.get("samples_failed") or 0)
            rows.append(
                {
                    "variant": lane.get("variant"),
                    "requested": lane_requested,
                    "succeeded": int(lane.get("samples_succeeded") or 0),
                    "failed": lane_failed,
                    "failure_rate": lane_failed / lane_requested if lane_requested else None,
                    "matched_post_jsd": quality.get("matched_post_jsd"),
                    "matched_post_kl": quality.get("matched_post_kl"),
                    "perplexity": quality.get("perplexity"),
                }
            )
        leaderboard = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "baseline_failure_rate": self.baseline_failure_rate,
            "failure_rate": failure_rate,
            "failure_rate_delta_vs_baseline": (
                failure_rate - self.baseline_failure_rate if failure_rate is not None else None
            ),
            "requested": requested,
            "succeeded": succeeded,
            "failed": failed,
            "adaptive_actions": len(self._actions),
            "rows": rows,
        }
        _write_json(self.run_dir / "leaderboard.json", leaderboard)
        _write_json(
            self.run_dir / "latest_summary.json",
            {
                "e2e_summary_path": e2e_summary.get("run_dir"),
                "leaderboard": leaderboard,
                "top_failure_cluster": clusters["clusters"][0] if clusters["clusters"] else None,
            },
        )
        return leaderboard
