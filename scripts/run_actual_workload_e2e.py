"""Actual model-backed e2e runner for encoding profiles.

Uses real prepared posts with angles, runs StegoPipeline model generation,
ReceiverPipeline payload recovery, and divergence metrics. This runner does not
fabricate synthetic posts or bypass model encode/decode work.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loguru import logger  # noqa: E402

from infrastructure.config import (  # noqa: E402
    get_workflow_encoding_secret,
    get_workflow_encoding_settings,
    override_workflow_context_sampler,
)
from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from services.stego_benchmark_service import (  # noqa: E402
    build_experiment_summary_metrics,
    build_sample_experiment_metrics,
)
from services.stego_experiment_service import (  # noqa: E402
    ExperimentVariant,
    applied_experiment_variant,
    resolve_experiment_variants,
)
from services.stego_feedback_service import (  # noqa: E402
    AdaptiveSampleState,
    StegoFeedbackRun,
    classify_failure,
    plan_adaptive_action,
    summarize_input_post,
    summarize_receiver_decode,
    summarize_stego_result,
)
from services.stego_metrics_service import (  # noqa: E402
    run_divergence_metrics,
    run_perplexity_metrics,
)
from workflows.pipelines.receiver import (  # noqa: E402
    ReceiverPipeline,
    nested_angles_from_post,
)
from workflows.pipelines.stego import StegoPipeline  # noqa: E402
from workflows.utils.naturalness_gate import (  # noqa: E402
    filter_angles_for_post,
    naturalness_gate_enabled,
)
from workflows.utils.output_results_shape import n8n_save_object_body  # noqa: E402
from workflows.utils.protocol_utils import stable_hash  # noqa: E402

RUNS_ROOT = _REPO_ROOT / "metrics" / "e2e_runs"
FEEDBACK_RUNS_ROOT = _REPO_ROOT / "metrics" / "feedback_runs"
NATURALNESS_EXPERIMENT_ROOT = _REPO_ROOT / "metrics" / "experiments" / "naturalness_gate_v1"
DEFAULT_VARIANTS = (
    "balanced",
    "capacity",
    "security_legacy",
    "sec_v2_anchored",
    "sec_v2_guided_natural",
    "sec_v2_natural_then_anchor_retry",
    "sec_v2_guided_natural_hybrid_extract",
    "sec_v2_natural_then_anchor_retry_hybrid_extract",
)
TRANSIENT_SAMPLE_ERROR_MARKERS = (
    "Connection aborted",
    "RemoteDisconnected",
    "Failed to resolve",
    "NameResolutionError",
    "Max retries exceeded",
    "Temporary failure in name resolution",
    "Read timed out",
    "ConnectTimeout",
    "ConnectionError",
    "SSLError",
    "ProtocolError",
)
RETRYABLE_STEGO_OUTPUT_ERROR_MARKERS = ("Stego LLM output must be valid JSON",)
DEFAULT_MAX_TRANSIENT_SAMPLE_RETRIES = 3
DEFAULT_TRANSIENT_SAMPLE_RETRY_BASE_DELAY_SECONDS = 30.0
DEFAULT_MAX_ADAPTIVE_SAMPLE_RETRIES = 2
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
)


@contextmanager
def _temporary_env(overrides: dict[str, str]):
    old_values = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def angle_path_for_post_id(angles_dir: Path, post_id: str) -> Path:
    """Resolve a legacy ``<post>.json`` or isolated ``<post>_<tag>.json`` artifact."""
    direct = angles_dir / f"{post_id}.json"
    if direct.exists():
        return direct
    tagged = sorted(angles_dir.glob(f"{post_id}_*.json"))
    if len(tagged) == 1:
        return tagged[0]
    if not tagged:
        raise FileNotFoundError(f"No angle artifact found for post ID {post_id!r} in {angles_dir}")
    raise ValueError(f"Ambiguous tagged angle artifacts for post ID {post_id!r}: {tagged}")


def _metric_progress(label: str, current: int, total: int) -> None:
    logger.bind(component="ActualWorkloadE2E").info(
        "metric_progress",
        label=label,
        current=current,
        total=total,
    )


def has_usable_angles(post: dict[str, Any]) -> bool:
    angles = post.get("angles")
    return isinstance(angles, list) and bool(angles)


def angle_artifact_identity(post: dict[str, Any]) -> dict[str, Any]:
    """Return an explicit identity for both versioned and historical angle posts."""
    artifact = post.get("angle_artifact")
    if not isinstance(artifact, dict):
        return {
            "schema_version": 1,
            "artifact_namespace": "legacy_unversioned",
            "generator_version": "legacy_unversioned",
            "sampler_version": "legacy_unversioned",
        }
    return {
        "schema_version": artifact.get("schema_version"),
        "artifact_namespace": artifact.get("artifact_namespace"),
        "generator_version": artifact.get("generator_version"),
        "sampler_version": artifact.get("sampler_version"),
        "capacity_profile": artifact.get("capacity_profile"),
        "capacity_limits": artifact.get("capacity_limits"),
        "generation_mode": artifact.get("generation_mode"),
        "angles_retained_target": artifact.get("angles_retained_target"),
        "angles_raw_target": artifact.get("angles_raw_target"),
    }


def validate_angle_artifact_identity(angles_dir: Path, post_ids: Sequence[str]) -> dict[str, Any]:
    """Reject a sample lane that silently combines different angle-generation versions."""
    identities: dict[str, dict[str, Any]] = {}
    for post_id in dict.fromkeys(post_ids):
        post = read_json(angle_path_for_post_id(angles_dir, post_id))
        identity = angle_artifact_identity(post)
        key = json.dumps(identity, sort_keys=True, ensure_ascii=True)
        identities[key] = identity
    if len(identities) != 1:
        raise ValueError(
            "Mixed angle artifact identities are not allowed in one sample-generation lane: "
            f"{list(identities.values())}"
        )
    return next(iter(identities.values()))


def _flatten_post_angles(post: dict[str, Any]) -> list[dict[str, Any]]:
    angles = post.get("angles")
    if not isinstance(angles, list):
        return []
    out: list[dict[str, Any]] = []
    for item in angles:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, list):
            out.extend(angle for angle in item if isinstance(angle, dict))
    return out


def _apply_e2e_angle_relevance_gate(
    post: dict[str, Any],
    baseline_post: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not naturalness_gate_enabled():
        return post, {"enabled": False}
    angles = _flatten_post_angles(post)
    filtered, report = filter_angles_for_post(angles, baseline_post | post)
    filtered_post = dict(post)
    filtered_post["angles"] = filtered
    filtered_post["options_count"] = len(filtered)
    return filtered_post, report


def _aggregate_angle_gate_reports(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for item in items:
        envelope = item.get("feedback_envelope")
        if not isinstance(envelope, dict):
            envelope = item.get("envelope")
        if not isinstance(envelope, dict):
            continue
        report = envelope.get("angle_relevance_gate")
        if isinstance(report, dict) and report.get("enabled"):
            reports.append(report)
    reason_counts: dict[str, int] = {}
    for report in reports:
        raw_counts = report.get("reason_counts", {})
        if not isinstance(raw_counts, dict):
            continue
        for key, value in raw_counts.items():
            if isinstance(value, int):
                reason_counts[str(key)] = reason_counts.get(str(key), 0) + value

    def _sum_int(key: str) -> int:
        total = 0
        for report in reports:
            value = report.get(key, 0)
            if isinstance(value, int):
                total += value
        return total

    return {
        "enabled": naturalness_gate_enabled(),
        "samples_with_report": len(reports),
        "input_count": _sum_int("input_count"),
        "kept_count": _sum_int("kept_count"),
        "rejected_count": _sum_int("rejected_count"),
        "reason_counts": reason_counts,
    }


def _cycle_to_length(items: Sequence[str], length: int) -> list[str]:
    if not items:
        return []
    return [items[idx % len(items)] for idx in range(length)]


def select_post_ids(
    *,
    explicit_post_ids: Sequence[str],
    angles_dir: Path,
    dataset_dir: Path,
    samples_per_profile: int,
    allow_post_reuse: bool,
) -> list[str]:
    cleaned = [Path(post_id.strip()).stem for post_id in explicit_post_ids if post_id.strip()]
    if cleaned:
        if len(cleaned) >= samples_per_profile:
            return cleaned[:samples_per_profile]
        if allow_post_reuse:
            return _cycle_to_length(cleaned, samples_per_profile)
        return cleaned
    selected: list[str] = []
    for path in sorted(angles_dir.glob("*.json")):
        if len(selected) >= samples_per_profile and not allow_post_reuse:
            break
        post_id = str(path.stem).split("_", maxsplit=1)[0]
        baseline_path = dataset_dir / f"{post_id}.json"
        if not baseline_path.exists():
            continue
        try:
            post = read_json(path)
        except Exception:
            continue
        if has_usable_angles(post):
            selected.append(post_id)
    if len(selected) < samples_per_profile:
        if allow_post_reuse and selected:
            return _cycle_to_length(selected, samples_per_profile)
        raise ValueError(
            f"Only found {len(selected)} usable real posts, need {samples_per_profile}."
        )
    return selected[:samples_per_profile]


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _payload_for(run_id: str, profile: str, post_id: str, sample_idx: int) -> str:
    return f"actual-e2e:{run_id}:{profile}:{sample_idx:04d}:{post_id}"


def _is_retryable_sample_error(exc: BaseException) -> bool:
    message = " ".join(str(part) for part in exc.args) if exc.args else str(exc)
    normalized = message.lower()
    return any(marker.lower() in normalized for marker in TRANSIENT_SAMPLE_ERROR_MARKERS)


def _is_retryable_stego_output_error(exc: BaseException) -> bool:
    message = " ".join(str(part) for part in exc.args) if exc.args else str(exc)
    normalized = message.lower()
    return any(marker.lower() in normalized for marker in RETRYABLE_STEGO_OUTPUT_ERROR_MARKERS)


def _transient_sample_retry_delay_seconds(retry_index: int, *, base_delay_seconds: float) -> float:
    if base_delay_seconds <= 0:
        return 0.0
    return base_delay_seconds * (2**retry_index)


def _run_receiver_decode(
    *,
    receiver: ReceiverPipeline,
    post: dict[str, Any],
    stego_result: dict[str, Any],
    payload: str,
    max_padding_bits: int = 256,
) -> dict[str, Any]:
    embedding = stego_result.get("embedding")
    compressed = None
    if isinstance(embedding, dict):
        compression = embedding.get("compression")
        if isinstance(compression, dict) and isinstance(compression.get("compressed"), str):
            compressed = compression["compressed"]
    decoded, info = receiver.decode_payload(
        stego_text=str(stego_result.get("stego_text", "")),
        rebuilt_post=post,
        pre_sender_post=post,
        nested_angles=nested_angles_from_post(post),
        compressed_full=compressed,
        max_padding_bits=max_padding_bits,
        strict_mode=False,
        expected_angle_index=_safe_int(stego_result.get("angle_index")),
        payload_transform=(
            stego_result.get("sender_audit", {}).get("payload_transform")
            if isinstance(stego_result.get("sender_audit"), dict)
            else None
        ),
    )
    if decoded != payload:
        raise RuntimeError("Receiver decoded a different payload than the sender embedded.")
    return info


def _run_sample(
    *,
    run_id: str,
    variant: ExperimentVariant,
    post_id: str,
    sample_idx: int,
    angles_dir: Path,
    dataset_dir: Path,
    input_dir: Path,
    profile_dataset_dir: Path,
    output_dir: Path,
    stego: StegoPipeline,
    receiver: ReceiverPipeline,
    max_retries: int,
    skip_receiver_decode: bool,
    max_transient_sample_retries: int,
    transient_sample_retry_base_delay_seconds: float,
    feedback_run: StegoFeedbackRun | None = None,
    adaptive_feedback: bool = False,
    max_adaptive_sample_retries: int = DEFAULT_MAX_ADAPTIVE_SAMPLE_RETRIES,
) -> dict[str, Any]:
    sample_label = f"{post_id}_version_{variant.name}_{sample_idx:04d}"
    payload = _payload_for(run_id, variant.name, post_id, sample_idx)
    payload_hash = stable_hash(payload)
    attempt_index = 0
    adaptive_attempts = 0
    adaptive_state = AdaptiveSampleState()

    while True:
        t0 = time.perf_counter()
        envelope: dict[str, Any] = {
            "profile": variant.base_profile,
            "variant": variant.name,
            "post_id": post_id,
            "sample_index": sample_idx,
            "payload_hash": payload_hash,
            "sample_attempt": attempt_index + 1,
            "adaptive_attempt": adaptive_attempts,
        }
        try:
            post = read_json(angle_path_for_post_id(angles_dir, post_id))
            baseline_post = read_json(dataset_dir / f"{post_id}.json")
            envelope.update(summarize_input_post(post, baseline_post))
            post, angle_gate_report = _apply_e2e_angle_relevance_gate(post, baseline_post)
            envelope["angle_relevance_gate"] = angle_gate_report
            if not has_usable_angles(post):
                raise ValueError(f"Post {post_id} has no usable angles")
            _write_json(input_dir / f"{sample_label}.json", post)
            _write_json(profile_dataset_dir / f"{post_id}.json", baseline_post)

            logger.bind(component="ActualWorkloadE2E").info(
                "sample_start",
                profile=variant.base_profile,
                variant=variant.name,
                post_id=post_id,
                sample_index=sample_idx,
                payload_hash=payload_hash,
                sample_attempt=attempt_index + 1,
            )
            with _temporary_env(adaptive_state.env_overrides):
                stego_result = stego.encode(
                    payload=payload,
                    post=post,
                    tag=f"version_{variant.name}",
                    max_retries=max_retries + adaptive_state.stego_max_retries_bonus,
                )
            envelope["stego_encode"] = summarize_stego_result(stego_result)
            if not stego_result.get("succeeded") or not stego_result.get("stego_text"):
                raise RuntimeError(str(stego_result.get("error") or "stego encode failed"))
            receiver_info = {}
            if not skip_receiver_decode:
                receiver_info = _run_receiver_decode(
                    receiver=receiver,
                    post=post,
                    stego_result=stego_result,
                    payload=payload,
                    max_padding_bits=adaptive_state.max_padding_bits,
                )
            envelope["receiver_decode"] = summarize_receiver_decode(receiver_info)
            output_path = output_dir / f"{sample_label}.json"
            _write_json(output_path, n8n_save_object_body(stego_result))
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            stego_text = str(stego_result.get("stego_text", ""))
            entry = {
                "profile": variant.base_profile,
                "variant": variant.name,
                "post_id": post_id,
                "sample_index": sample_idx,
                "payload_hash": payload_hash,
                "payload_bytes": len(payload.encode("utf-8")),
                "output_file": str(output_path),
                "elapsed_ms": elapsed_ms,
                "angle_index": stego_result.get("angle_index"),
                "retry_count": stego_result.get("retry_count"),
                "receiver_decode": receiver_info,
                "sample_attempt": attempt_index + 1,
                "transient_retry_count": attempt_index,
                "adaptive_retry_count": adaptive_attempts,
                "adaptive_actions": list(adaptive_state.actions),
                "feedback_envelope": envelope,
                "sample_metrics": build_sample_experiment_metrics(
                    stego_result,
                    stego_text=stego_text,
                    payload_bytes=len(payload.encode("utf-8")),
                    receiver_decode=receiver_info or None,
                ),
            }
            logger.bind(component="ActualWorkloadE2E").info(
                "sample_complete",
                profile=variant.base_profile,
                variant=variant.name,
                post_id=post_id,
                sample_index=sample_idx,
                elapsed_ms=elapsed_ms,
                angle_index=stego_result.get("angle_index"),
                retry_count=stego_result.get("retry_count"),
                sample_attempt=attempt_index + 1,
                transient_retry_count=attempt_index,
            )
            if feedback_run is not None:
                feedback_run.record_event(
                    {
                        **envelope,
                        "outcome": "succeeded",
                        "elapsed_ms": elapsed_ms,
                        "failure_code": None,
                    }
                )
            return entry
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            failure_code = classify_failure(f"{type(exc).__name__}: {exc}", envelope=envelope)
            if feedback_run is not None:
                feedback_run.record_event(
                    {
                        **envelope,
                        "outcome": "failed",
                        "elapsed_ms": elapsed_ms,
                        "failure_code": failure_code,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if adaptive_feedback and adaptive_attempts < max_adaptive_sample_retries:
                action = plan_adaptive_action(failure_code, adaptive_state)
                if action is not None:
                    adaptive_attempts += 1
                    action_payload = {
                        "profile": variant.base_profile,
                        "variant": variant.name,
                        "post_id": post_id,
                        "sample_index": sample_idx,
                        "failure_code": failure_code,
                        "adaptive_attempt": adaptive_attempts,
                        **action,
                    }
                    adaptive_state.actions.append(action_payload)
                    if feedback_run is not None:
                        feedback_run.record_action(action_payload)
                    logger.bind(component="ActualWorkloadE2E").warning(
                        "sample_adaptive_retrying_after_failure",
                        profile=variant.base_profile,
                        variant=variant.name,
                        post_id=post_id,
                        sample_index=sample_idx,
                        elapsed_ms=elapsed_ms,
                        failure_code=failure_code,
                        adaptive_attempt=adaptive_attempts,
                        action=action.get("action"),
                    )
                    continue
            should_retry = _is_retryable_sample_error(exc) or _is_retryable_stego_output_error(exc)
            if attempt_index < max_transient_sample_retries and should_retry:
                wait_seconds = _transient_sample_retry_delay_seconds(
                    attempt_index,
                    base_delay_seconds=(
                        0.0
                        if _is_retryable_stego_output_error(exc)
                        else transient_sample_retry_base_delay_seconds
                    ),
                )
                logger.bind(component="ActualWorkloadE2E").warning(
                    "sample_retrying_after_failure",
                    profile=variant.base_profile,
                    variant=variant.name,
                    post_id=post_id,
                    sample_index=sample_idx,
                    elapsed_ms=elapsed_ms,
                    sample_attempt=attempt_index + 1,
                    transient_retry_count=attempt_index,
                    wait_seconds=wait_seconds,
                    retryable_failure_kind=(
                        "stego_output" if _is_retryable_stego_output_error(exc) else "transient"
                    ),
                    error=f"{type(exc).__name__}: {exc}",
                )
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                attempt_index += 1
                continue
            # Out-of-band transport: the envelope has to survive the re-raise up to
            # run_profile, which reads it back with a matching getattr.
            setattr(exc, "feedback_envelope", envelope)  # noqa: B010
            raise


def run_profile(
    *,
    run_id: str,
    variant: ExperimentVariant,
    post_ids: Sequence[str],
    run_dir: Path,
    angles_dir: Path,
    dataset_dir: Path,
    max_retries: int,
    force_model_generation: bool,
    skip_receiver_decode: bool,
    fail_fast: bool,
    max_transient_sample_retries: int,
    transient_sample_retry_base_delay_seconds: float,
    feedback_run: StegoFeedbackRun | None = None,
    adaptive_feedback: bool = False,
    max_adaptive_sample_retries: int = DEFAULT_MAX_ADAPTIVE_SAMPLE_RETRIES,
) -> dict[str, Any]:
    effective_force_model_generation = (
        variant.real_force_model_generation
        if variant.real_force_model_generation is not None
        else force_model_generation
    )
    with applied_experiment_variant(
        variant,
        force_model_generation=effective_force_model_generation,
        default_secret="actual-workload-e2e-security-profile-secret",
    ):
        settings = get_workflow_encoding_settings()
        profile_dir = run_dir / variant.name
        input_dir = profile_dir / "input-angles"
        profile_dataset_dir = profile_dir / "dataset"
        output_dir = profile_dir / "output-results"
        failures_dir = profile_dir / "failures"
        metrics_dir = profile_dir / "metrics"
        for path in (input_dir, profile_dataset_dir, output_dir, failures_dir, metrics_dir):
            path.mkdir(parents=True, exist_ok=True)

        stego = StegoPipeline()
        receiver = ReceiverPipeline()
        entries: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        logger.bind(component="ActualWorkloadE2E").info(
            "profile_start",
            profile=variant.base_profile,
            variant=variant.name,
            samples=len(post_ids),
            settings=settings,
            force_model_generation=effective_force_model_generation,
            skip_receiver_decode=skip_receiver_decode,
        )

        for sample_idx, post_id in enumerate(post_ids):
            t0 = time.perf_counter()
            sample_label = f"{post_id}_version_{variant.name}_{sample_idx:04d}"
            try:
                entries.append(
                    _run_sample(
                        run_id=run_id,
                        variant=variant,
                        post_id=post_id,
                        sample_idx=sample_idx,
                        angles_dir=angles_dir,
                        dataset_dir=dataset_dir,
                        input_dir=input_dir,
                        profile_dataset_dir=profile_dataset_dir,
                        output_dir=output_dir,
                        stego=stego,
                        receiver=receiver,
                        max_retries=max_retries,
                        skip_receiver_decode=skip_receiver_decode,
                        max_transient_sample_retries=max_transient_sample_retries,
                        transient_sample_retry_base_delay_seconds=(
                            transient_sample_retry_base_delay_seconds
                        ),
                        feedback_run=feedback_run,
                        adaptive_feedback=adaptive_feedback,
                        max_adaptive_sample_retries=max_adaptive_sample_retries,
                    )
                )
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                failure_code = classify_failure(f"{type(exc).__name__}: {exc}")
                failure: dict[str, Any] = {
                    "profile": variant.base_profile,
                    "variant": variant.name,
                    "post_id": post_id,
                    "sample_index": sample_idx,
                    "elapsed_ms": elapsed_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                    "failure_code": failure_code,
                }
                failure_envelope = getattr(exc, "feedback_envelope", None)
                if isinstance(failure_envelope, dict):
                    failure["envelope"] = failure_envelope
                failures.append(failure)
                _write_json(failures_dir / f"{sample_label}.json", failure)
                logger.bind(component="ActualWorkloadE2E").exception(
                    "sample_failed",
                    profile=variant.base_profile,
                    variant=variant.name,
                    post_id=post_id,
                    sample_index=sample_idx,
                    elapsed_ms=elapsed_ms,
                )
                if fail_fast:
                    raise

        divergence: dict[str, Any] | None = None
        perplexity: dict[str, Any] | None = None
        if entries:
            divergence = run_divergence_metrics(
                output_dir,
                profile_dataset_dir,
                metrics_dir,
                progress_hook=_metric_progress,
            )
            perplexity = run_perplexity_metrics(
                output_dir,
                metrics_dir,
                progress_hook=_metric_progress,
            )
        summary = {
            "profile": variant.base_profile,
            "variant": variant.name,
            "variant_description": variant.description,
            "variant_env_overrides": dict(variant.env_overrides),
            "settings": settings,
            "has_encoding_secret": bool(get_workflow_encoding_secret()),
            "requested_samples": len(post_ids),
            "samples_succeeded": len(entries),
            "samples_failed": len(failures),
            "force_model_generation": effective_force_model_generation,
            "skip_receiver_decode": skip_receiver_decode,
            "max_transient_sample_retries": max_transient_sample_retries,
            "transient_sample_retry_base_delay_seconds": (
                transient_sample_retry_base_delay_seconds
            ),
            "entries": entries,
            "failures": failures,
            "angle_relevance_gate": _aggregate_angle_gate_reports([*entries, *failures]),
            "metrics_report_path": divergence["report_path"] if divergence else None,
            "metrics_report": divergence["report"] if divergence else None,
            "perplexity_report_path": perplexity["report_path"] if perplexity else None,
            "perplexity_report": perplexity["report"] if perplexity else None,
        }
        summary["summary_metrics"] = build_experiment_summary_metrics(
            entries,
            divergence_report=divergence["report"] if divergence else None,
            perplexity_report=perplexity["report"] if perplexity else None,
        )
        _write_json(profile_dir / "summary.json", summary)
        logger.bind(component="ActualWorkloadE2E").info(
            "profile_complete",
            profile=variant.base_profile,
            variant=variant.name,
            samples_succeeded=len(entries),
            samples_failed=len(failures),
            metrics_report_path=summary["metrics_report_path"],
        )
        return summary


def _classify_failure(error_text: str) -> str:
    return classify_failure(error_text)


def _build_progress_payload(
    *,
    run_id: str,
    run_dir: Path,
    samples_per_profile: int,
    selected_post_ids: Sequence[str],
    variant_names: Sequence[str],
    profile_summaries: Sequence[dict[str, Any]],
    summary_path: Path,
) -> dict[str, Any]:
    lanes: list[dict[str, Any]] = []
    total_failures = {
        "infrastructure_failure": 0,
        "generation_failure": 0,
        "decode_failure": 0,
        "metric_failure": 0,
        "data_failure": 0,
        "judge_failure": 0,
    }
    for name, lane_summary in zip(variant_names, profile_summaries, strict=False):
        failures = lane_summary.get("failures")
        failures_list = failures if isinstance(failures, list) else []
        classified = {
            "infrastructure_failure": 0,
            "generation_failure": 0,
            "decode_failure": 0,
            "metric_failure": 0,
            "data_failure": 0,
            "judge_failure": 0,
        }
        for failure in failures_list:
            if not isinstance(failure, dict):
                continue
            failure_class = str(
                failure.get("failure_code") or _classify_failure(str(failure.get("error") or ""))
            )
            classified.setdefault(failure_class, 0)
            total_failures.setdefault(failure_class, 0)
            classified[failure_class] += 1
            total_failures[failure_class] += 1
        metrics = lane_summary.get("summary_metrics")
        quality = metrics.get("quality_metrics") if isinstance(metrics, dict) else {}
        lanes.append(
            {
                "lane_id": name,
                "lane_type": "variant",
                "requested": samples_per_profile,
                "succeeded": int(lane_summary.get("samples_succeeded") or 0),
                "failed": int(lane_summary.get("samples_failed") or 0),
                "infrastructure_failures": classified["infrastructure_failure"],
                "generation_failures": classified["generation_failure"],
                "decode_failures": classified["decode_failure"],
                "metric_failures": classified["metric_failure"],
                "data_failures": classified["data_failure"],
                "judge_failures": classified["judge_failure"],
                "receiver_success_rate": (
                    quality.get("receiver_success_rate") if isinstance(quality, dict) else None
                ),
                "matched_post_kl": quality.get("matched_post_kl")
                if isinstance(quality, dict)
                else None,
                "matched_post_jsd": quality.get("matched_post_jsd")
                if isinstance(quality, dict)
                else None,
                "perplexity": quality.get("perplexity") if isinstance(quality, dict) else None,
                "judge_naturalness_mean": None,
                "last_updated_utc": datetime.now(UTC).isoformat(),
            }
        )
    any_failed = any(int(item.get("failed") or 0) > 0 for item in lanes)
    all_met_target = all(int(item.get("succeeded") or 0) >= samples_per_profile for item in lanes)
    status = "complete" if all_met_target else ("blocked" if any_failed else "running")
    return {
        "run_id": run_id,
        "track": "sample_generation",
        "status": status,
        "stage": "pilot" if samples_per_profile <= 25 else "main",
        "target_successful_samples_per_lane": samples_per_profile,
        "git_commit": "",
        "git_branch": "",
        "git_status_clean": False,
        "dataset_manifest": {"post_ids": list(selected_post_ids)},
        "lanes": lanes,
        "artifacts": {
            "post_ids": str((run_dir / "post_ids.json").resolve()),
            "summary": str(summary_path.resolve()),
            "leaderboard": "",
            "frontier": "",
            "judge_samples": "",
            "latest_heartbeat": str((RUNS_ROOT / "latest_actual_workload_e2e.json").resolve()),
        },
        "blockers": [],
        "next_action": (
            "Run metrics rerun for failed samples and retry infrastructure failures with same post IDs."
            if any_failed
            else "Promote to next sample size stage."
        ),
        "failure_totals": total_failures,
    }


def run_actual_workload_e2e(
    *,
    profiles: Sequence[str],
    variants: Sequence[str] | None = None,
    samples_per_profile: int,
    post_ids: Sequence[str],
    angles_dir: Path,
    dataset_dir: Path,
    run_dir: Path | None,
    overwrite: bool,
    max_retries: int,
    force_model_generation: bool,
    skip_receiver_decode: bool,
    allow_post_reuse: bool,
    fail_fast: bool,
    max_transient_sample_retries: int = DEFAULT_MAX_TRANSIENT_SAMPLE_RETRIES,
    transient_sample_retry_base_delay_seconds: float = (
        DEFAULT_TRANSIENT_SAMPLE_RETRY_BASE_DELAY_SECONDS
    ),
    feedback_run_dir: Path | None = None,
    adaptive_feedback: bool = False,
    max_adaptive_sample_retries: int = DEFAULT_MAX_ADAPTIVE_SAMPLE_RETRIES,
) -> dict[str, Any]:
    if samples_per_profile <= 0:
        raise ValueError("samples_per_profile must be positive")
    variant_names = list(variants or profiles)
    resolved_variants = resolve_experiment_variants(variant_names)
    selected_post_ids = select_post_ids(
        explicit_post_ids=post_ids,
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        samples_per_profile=samples_per_profile,
        allow_post_reuse=allow_post_reuse,
    )[:samples_per_profile]
    angle_artifact = validate_angle_artifact_identity(angles_dir, selected_post_ids)
    created = datetime.now(UTC)
    run_id = created.strftime("%Y%m%dT%H%M%SZ")
    resolved_run_dir = (run_dir or RUNS_ROOT / f"actual_workload_e2e_{run_id}").resolve()
    if resolved_run_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"Run directory already exists: {resolved_run_dir}. Use --overwrite."
            )
        shutil.rmtree(resolved_run_dir)
    resolved_run_dir.mkdir(parents=True, exist_ok=True)
    resolved_feedback_dir = feedback_run_dir
    if resolved_feedback_dir is None and adaptive_feedback:
        resolved_feedback_dir = FEEDBACK_RUNS_ROOT / f"feedback_{run_id}"
    feedback_run = (
        StegoFeedbackRun(resolved_feedback_dir.resolve())
        if resolved_feedback_dir is not None
        else None
    )

    profile_summaries: list[dict[str, Any]] = []
    logger.bind(component="ActualWorkloadE2E").info(
        "actual_workload_run_start",
        run_dir=str(resolved_run_dir),
        profiles=list(profiles),
        variants=variant_names,
        samples_per_profile=samples_per_profile,
        post_ids=selected_post_ids,
        max_retries=max_retries,
        max_transient_sample_retries=max_transient_sample_retries,
        transient_sample_retry_base_delay_seconds=transient_sample_retry_base_delay_seconds,
        feedback_run_dir=str(feedback_run.run_dir) if feedback_run else None,
        adaptive_feedback=adaptive_feedback,
    )
    for variant in resolved_variants:
        profile_summaries.append(
            run_profile(
                run_id=run_id,
                variant=variant,
                post_ids=selected_post_ids,
                run_dir=resolved_run_dir,
                angles_dir=angles_dir,
                dataset_dir=dataset_dir,
                max_retries=max_retries,
                force_model_generation=force_model_generation,
                skip_receiver_decode=skip_receiver_decode,
                fail_fast=fail_fast,
                max_transient_sample_retries=max_transient_sample_retries,
                transient_sample_retry_base_delay_seconds=(
                    transient_sample_retry_base_delay_seconds
                ),
                feedback_run=feedback_run,
                adaptive_feedback=adaptive_feedback,
                max_adaptive_sample_retries=max_adaptive_sample_retries,
            )
        )

    total_succeeded = sum(int(summary["samples_succeeded"]) for summary in profile_summaries)
    total_failed = sum(int(summary["samples_failed"]) for summary in profile_summaries)
    summary = {
        "run_id": run_id,
        "created_at_utc": created.isoformat(),
        "run_dir": str(resolved_run_dir),
        "profiles": list(profiles),
        "variants": variant_names,
        "samples_per_profile": samples_per_profile,
        "selected_post_ids": selected_post_ids,
        "unique_selected_post_ids": sorted(set(selected_post_ids)),
        "total_requested_samples": len(resolved_variants) * samples_per_profile,
        "total_succeeded_samples": total_succeeded,
        "total_failed_samples": total_failed,
        "source": {
            "kind": "prepared_real_posts_with_angles",
            "angles_dir": str(angles_dir.resolve()),
            "dataset_dir": str(dataset_dir.resolve()),
            "angle_artifact": angle_artifact,
        },
        "force_model_generation": force_model_generation,
        "skip_receiver_decode": skip_receiver_decode,
        "allow_post_reuse": allow_post_reuse,
        "max_retries": max_retries,
        "max_transient_sample_retries": max_transient_sample_retries,
        "transient_sample_retry_base_delay_seconds": transient_sample_retry_base_delay_seconds,
        "feedback_run_dir": str(feedback_run.run_dir) if feedback_run else None,
        "adaptive_feedback": adaptive_feedback,
        "max_adaptive_sample_retries": max_adaptive_sample_retries,
        "profile_summaries": profile_summaries,
    }
    _write_json(resolved_run_dir / "summary.json", summary)
    _write_json(resolved_run_dir / "post_ids.json", {"post_ids": selected_post_ids})
    progress = _build_progress_payload(
        run_id=run_id,
        run_dir=resolved_run_dir,
        samples_per_profile=samples_per_profile,
        selected_post_ids=selected_post_ids,
        variant_names=variant_names,
        profile_summaries=profile_summaries,
        summary_path=resolved_run_dir / "summary.json",
    )
    _write_json(resolved_run_dir / "progress.json", progress)
    _write_json(RUNS_ROOT / "latest_actual_workload_e2e.json", summary)
    if feedback_run is not None:
        feedback_run.finalize(summary)
    logger.bind(component="ActualWorkloadE2E").info(
        "actual_workload_run_complete",
        run_dir=str(resolved_run_dir),
        total_succeeded_samples=total_succeeded,
        total_failed_samples=total_failed,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Run actual model-backed stego/receiver/metrics e2e on real prepared posts.")
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=None,
        help="Base profile to run. Repeat for multiple profiles.",
    )
    parser.add_argument(
        "--variant",
        action="append",
        default=None,
        help="Named experiment variant to run. Repeat for multiple variants.",
    )
    parser.add_argument("--samples-per-profile", type=int, default=1)
    parser.add_argument("--post-id", action="append", default=[])
    parser.add_argument("--angles-dir", default=str(_REPO_ROOT / "datasets" / "news_angles"))
    parser.add_argument("--dataset-dir", default=str(_REPO_ROOT / "datasets" / "news_cleaned"))
    parser.add_argument(
        "--context-sampler",
        choices=("context_weighted_v2", "post_level_v1"),
        default="post_level_v1",
        help="Sampler used to reconstruct LUCID or legacy angle contexts.",
    )
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument(
        "--allow-post-reuse",
        action="store_true",
        help="Cycle usable real posts when samples-per-profile exceeds available posts.",
    )
    parser.add_argument(
        "--profile-default-generation",
        action="store_true",
        help="Use each profile's configured generation modes instead of forcing model mode.",
    )
    parser.add_argument("--skip-receiver-decode", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--max-transient-sample-retries",
        type=int,
        default=DEFAULT_MAX_TRANSIENT_SAMPLE_RETRIES,
    )
    parser.add_argument(
        "--transient-sample-retry-base-delay-seconds",
        type=float,
        default=DEFAULT_TRANSIENT_SAMPLE_RETRY_BASE_DELAY_SECONDS,
    )
    parser.add_argument(
        "--feedback-run-dir",
        default=None,
        help="Write adaptive feedback artifacts under this directory.",
    )
    parser.add_argument(
        "--adaptive-feedback",
        action="store_true",
        help="Enable per-sample adaptive retries and feedback artifacts.",
    )
    parser.add_argument(
        "--max-adaptive-sample-retries",
        type=int,
        default=DEFAULT_MAX_ADAPTIVE_SAMPLE_RETRIES,
    )
    parser.add_argument(
        "--progress-log",
        default=None,
        help="Optional JSONL log containing only ActualWorkloadE2E progress events.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    if args.progress_log:
        logger.add(
            args.progress_log,
            level="INFO",
            serialize=True,
            filter=lambda record: record["extra"].get("component") == "ActualWorkloadE2E",
        )
    profiles = tuple(args.profile or ("balanced",))
    variants = tuple(args.variant) if args.variant else tuple(args.profile or DEFAULT_VARIANTS)
    with override_workflow_context_sampler(args.context_sampler):
        run_actual_workload_e2e(
            profiles=profiles,
            variants=variants,
            samples_per_profile=args.samples_per_profile,
            post_ids=args.post_id,
            angles_dir=Path(args.angles_dir),
            dataset_dir=Path(args.dataset_dir),
            run_dir=Path(args.run_dir).resolve() if args.run_dir else None,
            overwrite=bool(args.overwrite),
            max_retries=args.max_retries,
            force_model_generation=not bool(args.profile_default_generation),
            skip_receiver_decode=bool(args.skip_receiver_decode),
            allow_post_reuse=bool(args.allow_post_reuse),
            fail_fast=bool(args.fail_fast),
            max_transient_sample_retries=args.max_transient_sample_retries,
            transient_sample_retry_base_delay_seconds=(args.transient_sample_retry_base_delay_seconds),
            feedback_run_dir=Path(args.feedback_run_dir) if args.feedback_run_dir else None,
            adaptive_feedback=bool(args.adaptive_feedback),
            max_adaptive_sample_retries=args.max_adaptive_sample_retries,
        )


if __name__ == "__main__":
    append_current_pid_to_log()
    main()
