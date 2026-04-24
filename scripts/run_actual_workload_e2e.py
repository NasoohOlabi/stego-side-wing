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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.config import (  # noqa: E402
    get_workflow_encoding_secret,
    get_workflow_encoding_settings,
)
from infrastructure.json_logging import configure_api_logging  # noqa: E402
from loguru import logger  # noqa: E402
from services.stego_metrics_service import run_divergence_metrics  # noqa: E402
from workflows.pipelines.receiver import (  # noqa: E402
    ReceiverPipeline,
    nested_angles_from_post,
)
from workflows.pipelines.stego import StegoPipeline  # noqa: E402
from workflows.utils.output_results_shape import n8n_save_object_body  # noqa: E402
from workflows.utils.protocol_utils import stable_hash  # noqa: E402

RUNS_ROOT = _REPO_ROOT / "metrics" / "e2e_runs"
DEFAULT_PROFILES = ("balanced", "robustness", "capacity", "security")
PROFILE_OVERRIDE_KEYS = (
    "WORKFLOW_ANGLES_GENERATION_MODE",
    "WORKFLOW_STEGO_GENERATION_MODE",
    "WORKFLOW_CAPACITY_PROFILE",
    "WORKFLOW_CAPACITY_LIMITS_ENABLED",
    "WORKFLOW_PAYLOAD_TRANSFORM",
    "WORKFLOW_STEGO_PROMPT_STYLE",
    "WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT",
    "WORKFLOW_STEGO_MAX_RETRIES",
    "WORKFLOW_DECODE_SEMANTIC_TOP_N",
    "WORKFLOW_DECODE_LLM_MAX_TRIES",
    "WORKFLOW_STEGO_LLM_TEMPERATURE",
    "WORKFLOW_DECODE_STRICT_DEFAULT",
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


@contextmanager
def _profile_env(profile: str, *, force_model_generation: bool) -> Iterator[None]:
    keys = ("WORKFLOW_ENCODING_PROFILE", *PROFILE_OVERRIDE_KEYS, "WORKFLOW_ENCODING_SECRET")
    old_values = {key: os.environ.get(key) for key in keys}
    try:
        for key in PROFILE_OVERRIDE_KEYS:
            os.environ.pop(key, None)
        os.environ["WORKFLOW_ENCODING_PROFILE"] = profile
        if force_model_generation:
            os.environ["WORKFLOW_ANGLES_GENERATION_MODE"] = "model"
            os.environ["WORKFLOW_STEGO_GENERATION_MODE"] = "model"
        if profile == "security":
            os.environ.setdefault(
                "WORKFLOW_ENCODING_SECRET",
                "actual-workload-e2e-security-profile-secret",
            )
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _metric_progress(label: str, current: int, total: int) -> None:
    logger.bind(component="ActualWorkloadE2E").info(
        "metric_progress",
        label=label,
        current=current,
        total=total,
    )


def _has_usable_angles(post: dict[str, Any]) -> bool:
    angles = post.get("angles")
    return isinstance(angles, list) and bool(angles)


def _cycle_to_length(items: Sequence[str], length: int) -> list[str]:
    if not items:
        return []
    return [items[idx % len(items)] for idx in range(length)]


def _select_post_ids(
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
        baseline_path = dataset_dir / path.name
        if not baseline_path.exists():
            continue
        try:
            post = _read_json(path)
        except Exception:
            continue
        if _has_usable_angles(post):
            selected.append(path.stem)
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


def _run_receiver_decode(
    *,
    receiver: ReceiverPipeline,
    post: dict[str, Any],
    stego_result: dict[str, Any],
    payload: str,
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


def _run_profile(
    *,
    run_id: str,
    profile: str,
    post_ids: Sequence[str],
    run_dir: Path,
    angles_dir: Path,
    dataset_dir: Path,
    max_retries: int,
    force_model_generation: bool,
    skip_receiver_decode: bool,
    fail_fast: bool,
) -> dict[str, Any]:
    with _profile_env(profile, force_model_generation=force_model_generation):
        settings = get_workflow_encoding_settings()
        profile_dir = run_dir / profile
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
            profile=profile,
            samples=len(post_ids),
            settings=settings,
            force_model_generation=force_model_generation,
            skip_receiver_decode=skip_receiver_decode,
        )

        for sample_idx, post_id in enumerate(post_ids):
            sample_label = f"{post_id}_version_{profile}_{sample_idx:04d}"
            payload = _payload_for(run_id, profile, post_id, sample_idx)
            t0 = time.perf_counter()
            try:
                post = _read_json(angles_dir / f"{post_id}.json")
                baseline_post = _read_json(dataset_dir / f"{post_id}.json")
                if not _has_usable_angles(post):
                    raise ValueError(f"Post {post_id} has no usable angles")
                _write_json(input_dir / f"{sample_label}.json", post)
                _write_json(profile_dataset_dir / f"{post_id}.json", baseline_post)

                logger.bind(component="ActualWorkloadE2E").info(
                    "sample_start",
                    profile=profile,
                    post_id=post_id,
                    sample_index=sample_idx,
                    payload_hash=stable_hash(payload),
                )
                stego_result = stego.encode(
                    payload=payload,
                    post=post,
                    tag=f"version_{profile}",
                    max_retries=max_retries,
                )
                if not stego_result.get("succeeded") or not stego_result.get("stego_text"):
                    raise RuntimeError(str(stego_result.get("error") or "stego encode failed"))
                receiver_info = {}
                if not skip_receiver_decode:
                    receiver_info = _run_receiver_decode(
                        receiver=receiver,
                        post=post,
                        stego_result=stego_result,
                        payload=payload,
                    )
                output_path = output_dir / f"{sample_label}.json"
                _write_json(output_path, n8n_save_object_body(stego_result))
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                entries.append(
                    {
                        "profile": profile,
                        "post_id": post_id,
                        "sample_index": sample_idx,
                        "payload_hash": stable_hash(payload),
                        "payload_bytes": len(payload.encode("utf-8")),
                        "output_file": str(output_path),
                        "elapsed_ms": elapsed_ms,
                        "angle_index": stego_result.get("angle_index"),
                        "retry_count": stego_result.get("retry_count"),
                        "receiver_decode": receiver_info,
                    }
                )
                logger.bind(component="ActualWorkloadE2E").info(
                    "sample_complete",
                    profile=profile,
                    post_id=post_id,
                    sample_index=sample_idx,
                    elapsed_ms=elapsed_ms,
                    angle_index=stego_result.get("angle_index"),
                    retry_count=stego_result.get("retry_count"),
                )
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                failure = {
                    "profile": profile,
                    "post_id": post_id,
                    "sample_index": sample_idx,
                    "elapsed_ms": elapsed_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                failures.append(failure)
                _write_json(failures_dir / f"{sample_label}.json", failure)
                logger.bind(component="ActualWorkloadE2E").exception(
                    "sample_failed",
                    profile=profile,
                    post_id=post_id,
                    sample_index=sample_idx,
                    elapsed_ms=elapsed_ms,
                )
                if fail_fast:
                    raise

        divergence: dict[str, Any] | None = None
        if entries:
            divergence = run_divergence_metrics(
                output_dir,
                profile_dataset_dir,
                metrics_dir,
                progress_hook=_metric_progress,
            )
        summary = {
            "profile": profile,
            "settings": settings,
            "has_encoding_secret": bool(get_workflow_encoding_secret()),
            "requested_samples": len(post_ids),
            "samples_succeeded": len(entries),
            "samples_failed": len(failures),
            "force_model_generation": force_model_generation,
            "skip_receiver_decode": skip_receiver_decode,
            "entries": entries,
            "failures": failures,
            "metrics_report_path": divergence["report_path"] if divergence else None,
            "metrics_report": divergence["report"] if divergence else None,
        }
        _write_json(profile_dir / "summary.json", summary)
        logger.bind(component="ActualWorkloadE2E").info(
            "profile_complete",
            profile=profile,
            samples_succeeded=len(entries),
            samples_failed=len(failures),
            metrics_report_path=summary["metrics_report_path"],
        )
        return summary


def run_actual_workload_e2e(
    *,
    profiles: Sequence[str],
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
) -> dict[str, Any]:
    if samples_per_profile <= 0:
        raise ValueError("samples_per_profile must be positive")
    selected_post_ids = _select_post_ids(
        explicit_post_ids=post_ids,
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        samples_per_profile=samples_per_profile,
        allow_post_reuse=allow_post_reuse,
    )[:samples_per_profile]
    created = datetime.now(UTC)
    run_id = created.strftime("%Y%m%dT%H%M%SZ")
    resolved_run_dir = (
        run_dir or RUNS_ROOT / f"actual_workload_e2e_{run_id}"
    ).resolve()
    if resolved_run_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"Run directory already exists: {resolved_run_dir}. Use --overwrite."
            )
        shutil.rmtree(resolved_run_dir)
    resolved_run_dir.mkdir(parents=True, exist_ok=True)

    profile_summaries = []
    logger.bind(component="ActualWorkloadE2E").info(
        "actual_workload_run_start",
        run_dir=str(resolved_run_dir),
        profiles=list(profiles),
        samples_per_profile=samples_per_profile,
        post_ids=selected_post_ids,
        max_retries=max_retries,
    )
    for profile in profiles:
        profile_summaries.append(
            _run_profile(
                run_id=run_id,
                profile=profile,
                post_ids=selected_post_ids,
                run_dir=resolved_run_dir,
                angles_dir=angles_dir,
                dataset_dir=dataset_dir,
                max_retries=max_retries,
                force_model_generation=force_model_generation,
                skip_receiver_decode=skip_receiver_decode,
                fail_fast=fail_fast,
            )
        )

    total_succeeded = sum(int(summary["samples_succeeded"]) for summary in profile_summaries)
    total_failed = sum(int(summary["samples_failed"]) for summary in profile_summaries)
    summary = {
        "run_id": run_id,
        "created_at_utc": created.isoformat(),
        "run_dir": str(resolved_run_dir),
        "profiles": list(profiles),
        "samples_per_profile": samples_per_profile,
        "selected_post_ids": selected_post_ids,
        "unique_selected_post_ids": sorted(set(selected_post_ids)),
        "total_requested_samples": len(profiles) * samples_per_profile,
        "total_succeeded_samples": total_succeeded,
        "total_failed_samples": total_failed,
        "source": {
            "kind": "prepared_real_posts_with_angles",
            "angles_dir": str(angles_dir.resolve()),
            "dataset_dir": str(dataset_dir.resolve()),
        },
        "force_model_generation": force_model_generation,
        "skip_receiver_decode": skip_receiver_decode,
        "allow_post_reuse": allow_post_reuse,
        "max_retries": max_retries,
        "profile_summaries": profile_summaries,
    }
    _write_json(resolved_run_dir / "summary.json", summary)
    _write_json(RUNS_ROOT / "latest_actual_workload_e2e.json", summary)
    logger.bind(component="ActualWorkloadE2E").info(
        "actual_workload_run_complete",
        run_dir=str(resolved_run_dir),
        total_succeeded_samples=total_succeeded,
        total_failed_samples=total_failed,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run actual model-backed stego/receiver/metrics e2e on real prepared posts."
        )
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=DEFAULT_PROFILES,
        default=None,
        help="Encoding profile to run. Repeat for multiple profiles.",
    )
    parser.add_argument("--samples-per-profile", type=int, default=1)
    parser.add_argument("--post-id", action="append", default=[])
    parser.add_argument("--angles-dir", default=str(_REPO_ROOT / "datasets" / "news_angles"))
    parser.add_argument("--dataset-dir", default=str(_REPO_ROOT / "datasets" / "news_cleaned"))
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
    run_actual_workload_e2e(
        profiles=tuple(args.profile or DEFAULT_PROFILES),
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
    )


if __name__ == "__main__":
    main()
