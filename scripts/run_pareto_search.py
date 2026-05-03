"""Orchestrate synthetic and real stego Pareto screening runs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from loguru import logger  # noqa: E402
from run_actual_workload_e2e import DEFAULT_VARIANTS, run_actual_workload_e2e  # noqa: E402
from run_encoding_config_e2e import run_encoding_config_e2e  # noqa: E402

RUNS_ROOT = _REPO_ROOT / "metrics" / "pareto_runs"
_PARETO_LOG = logger.bind(component="ParetoSearch")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _summary_rows(stage: str, result: dict[str, Any], *, payload_bytes: int | None) -> list[dict[str, Any]]:
    summaries = result.get("summaries") or result.get("profile_summaries") or []
    rows: list[dict[str, Any]] = []
    summary_list = summaries if isinstance(summaries, list) else []
    for summary in summary_list:
        if not isinstance(summary, dict):
            continue
        metrics = summary.get("summary_metrics")
        metrics_dict = metrics if isinstance(metrics, dict) else {}
        rows.append(
            {
                "stage": stage,
                "variant": summary.get("variant") or summary.get("profile"),
                "profile": summary.get("profile"),
                "payload_bytes": payload_bytes or summary.get("payload_bytes"),
                "samples_succeeded": summary.get("samples_succeeded", summary.get("samples")),
                "samples_failed": summary.get("samples_failed", 0),
                "quality_metrics": metrics_dict.get("quality_metrics", {}),
                "carrier_metrics": metrics_dict.get("carrier_metrics", {}),
                "selection_metrics": metrics_dict.get("selection_metrics", {}),
                "capacity_metrics": metrics_dict.get("capacity_metrics", {}),
                "run_dir": result.get("run_dir"),
            }
        )
    return rows


def _metric(row: dict[str, Any], group: str, key: str) -> float | None:
    value = row.get(group, {})
    if not isinstance(value, dict):
        return None
    raw = value.get(key)
    return float(raw) if isinstance(raw, (int, float)) else None


def _not_worse(lhs: float | None, rhs: float | None, *, maximize: bool) -> bool:
    if lhs is None:
        return False
    if rhs is None:
        return True
    return lhs >= rhs if maximize else lhs <= rhs


def _better(lhs: float | None, rhs: float | None, *, maximize: bool) -> bool:
    if lhs is None:
        return False
    if rhs is None:
        return True
    return lhs > rhs if maximize else lhs < rhs


def _dominates(lhs: dict[str, Any], rhs: dict[str, Any]) -> bool:
    objectives = (
        ("quality_metrics", "receiver_success_rate", True),
        ("quality_metrics", "matched_post_kl", False),
        ("quality_metrics", "matched_post_jsd", False),
        ("carrier_metrics", "hidden_expansion_ratio", False),
        ("carrier_metrics", "standard_fallback_rate", False),
        ("selection_metrics", "unique_selection_signatures", True),
    )
    comparisons = [
        (
            _metric(lhs, group, key),
            _metric(rhs, group, key),
            maximize,
        )
        for group, key, maximize in objectives
    ]
    return all(_not_worse(a, b, maximize=m) for a, b, m in comparisons) and any(
        _better(a, b, maximize=m) for a, b, m in comparisons
    )


def build_frontier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row.get("stage") == "real_screen"]
    if not candidates:
        candidates = [row for row in rows if str(row.get("stage", "")).startswith("synthetic")]
    return [
        row
        for row in candidates
        if not any(other is not row and _dominates(other, row) for other in candidates)
    ]


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -(_metric(row, "quality_metrics", "receiver_success_rate") or 0.0),
            _metric(row, "quality_metrics", "matched_post_kl") or float("inf"),
            _metric(row, "carrier_metrics", "hidden_expansion_ratio") or float("inf"),
        ),
    )


def _write_rollups(run_dir: Path, rows: list[dict[str, Any]], *, next_batch: str | None) -> None:
    created_at = datetime.now(UTC).isoformat()
    leaderboard = {"created_at_utc": created_at, "rows": _sort_rows(rows)}
    frontier = {"created_at_utc": created_at, "rows": _sort_rows(build_frontier(rows))}
    heartbeat = {
        "created_at_utc": created_at,
        "completed_lanes": [row.get("variant") for row in rows],
        "failure_count": sum(int(row.get("samples_failed") or 0) for row in rows),
        "frontier": frontier["rows"],
        "next_batch": next_batch,
    }
    _write_json(run_dir / "leaderboard.json", leaderboard)
    _write_json(run_dir / "frontier.json", frontier)
    _write_json(run_dir / "latest_heartbeat.json", heartbeat)


def _prepare_run_dir(run_dir: Path, *, overwrite: bool) -> Path:
    resolved = run_dir.resolve()
    if resolved.exists() and overwrite:
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _run_or_load_synthetic(
    *,
    run_dir: Path,
    variants: list[str],
    payload_bytes: int,
    samples: int,
    seed: int,
    compute_perplexity: bool,
    resume: bool,
) -> dict[str, Any]:
    stage_dir = run_dir / f"synthetic_{payload_bytes}b"
    summary_path = stage_dir / "summary.json"
    if resume and summary_path.is_file():
        return _read_json(summary_path)
    return run_encoding_config_e2e(
        profiles=variants,
        variants=variants,
        samples_per_profile=samples,
        payload_bytes=payload_bytes,
        run_dir=stage_dir,
        overwrite=True,
        seed=seed,
        max_primary_kl=1e-12,
        compute_perplexity=compute_perplexity,
        force_extractive_generation=True,
    )


def _run_or_load_real(
    *,
    run_dir: Path,
    variants: list[str],
    samples: int,
    max_retries: int,
    compute_receiver: bool,
    resume: bool,
) -> dict[str, Any]:
    stage_dir = run_dir / "real_screen"
    summary_path = stage_dir / "summary.json"
    if resume and summary_path.is_file():
        return _read_json(summary_path)
    return run_actual_workload_e2e(
        profiles=variants,
        variants=variants,
        samples_per_profile=samples,
        post_ids=[],
        angles_dir=_REPO_ROOT / "datasets" / "news_angles",
        dataset_dir=_REPO_ROOT / "datasets" / "news_cleaned",
        run_dir=stage_dir,
        overwrite=True,
        max_retries=max_retries,
        force_model_generation=True,
        skip_receiver_decode=not compute_receiver,
        allow_post_reuse=False,
        fail_fast=False,
        max_transient_sample_retries=3,
        transient_sample_retry_base_delay_seconds=30.0,
    )


def run_pareto_search(
    *,
    variants: list[str],
    run_dir: Path,
    synthetic_samples: int,
    synthetic_payload_sizes: list[int],
    real_samples: int,
    max_retries: int,
    compute_perplexity: bool,
    compute_receiver: bool,
    resume: bool,
    overwrite: bool,
) -> dict[str, Any]:
    resolved_run_dir = _prepare_run_dir(run_dir, overwrite=overwrite)
    rows: list[dict[str, Any]] = []
    _write_rollups(resolved_run_dir, rows, next_batch="synthetic_screen")
    for index, payload_bytes in enumerate(synthetic_payload_sizes):
        result = _run_or_load_synthetic(
            run_dir=resolved_run_dir,
            variants=variants,
            payload_bytes=payload_bytes,
            samples=synthetic_samples,
            seed=1337 + index,
            compute_perplexity=compute_perplexity,
            resume=resume,
        )
        rows.extend(_summary_rows(f"synthetic_{payload_bytes}b", result, payload_bytes=payload_bytes))
        _write_rollups(resolved_run_dir, rows, next_batch="real_screen")
    real = _run_or_load_real(
        run_dir=resolved_run_dir,
        variants=variants,
        samples=real_samples,
        max_retries=max_retries,
        compute_receiver=compute_receiver,
        resume=resume,
    )
    rows.extend(_summary_rows("real_screen", real, payload_bytes=None))
    _write_rollups(resolved_run_dir, rows, next_batch=None)
    summary = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_dir": str(resolved_run_dir),
        "variants": variants,
        "synthetic_payload_sizes": synthetic_payload_sizes,
        "synthetic_samples": synthetic_samples,
        "real_samples": real_samples,
        "rows": rows,
    }
    _write_json(resolved_run_dir / "summary.json", summary)
    return summary


def _parse_csv_ints(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run stego Pareto search stages.")
    parser.add_argument("--variant", action="append", default=[])
    parser.add_argument("--synthetic-samples", type=int, default=200)
    parser.add_argument("--synthetic-payload-sizes", default="49,96,512")
    parser.add_argument("--real-samples", type=int, default=25)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--compute-perplexity", action="store_true")
    parser.add_argument("--skip-receiver-decode", action="store_true")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    run_dir = (
        Path(args.run_dir)
        if args.run_dir
        else RUNS_ROOT / f"pareto_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    variants = [str(value) for value in (args.variant or DEFAULT_VARIANTS)]
    _PARETO_LOG.info("pareto_search_start", run_dir=str(run_dir), variants=variants)
    run_pareto_search(
        variants=variants,
        run_dir=run_dir,
        synthetic_samples=max(1, args.synthetic_samples),
        synthetic_payload_sizes=_parse_csv_ints(args.synthetic_payload_sizes),
        real_samples=max(1, args.real_samples),
        max_retries=max(0, args.max_retries),
        compute_perplexity=bool(args.compute_perplexity),
        compute_receiver=not bool(args.skip_receiver_decode),
        resume=bool(args.resume),
        overwrite=bool(args.overwrite),
    )


if __name__ == "__main__":
    main()
