#!/usr/bin/env python3
"""Resumable BARB vs balanced campaign runner with live progress telemetry.

Creates/continues a campaign under ``metrics/experiments/barb/runs/<run_id>/``.
Target is paired samples (same post_ids) across ``balanced`` and ``barb``.

Resume is default when ``campaign.json`` already exists: completed sample
outputs are skipped. Use ``--overwrite`` only to wipe and restart.

Telemetry artifacts (updated after every sample):
  - progress.json   machine-readable counters + current work item
  - heartbeat.json  liveness stamp + phase
  - status.txt      one-screen human summary
  - events.jsonl    append-only event log
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"
for _path in (_SRC, _SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from loguru import logger  # noqa: E402

import run_actual_workload_e2e as e2e  # noqa: E402
from infrastructure.config import override_workflow_context_sampler  # noqa: E402
from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from services.stego_experiment_service import (  # noqa: E402
    applied_experiment_variant,
    resolve_experiment_variants,
)
from workflows.pipelines.receiver import ReceiverPipeline  # noqa: E402
from workflows.pipelines.stego import StegoPipeline  # noqa: E402

_LOG = logger.bind(component="BarbCampaign")
BARB_RUNS_ROOT = _REPO_ROOT / "metrics" / "experiments" / "barb" / "runs"
DEFAULT_VARIANTS = ("balanced", "barb")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_event(run_dir: Path, event: str, **fields: Any) -> None:
    row = {"ts": _utc_now(), "event": event, **fields}
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _sample_label(post_id: str, variant: str, sample_idx: int) -> str:
    return f"{post_id}_version_{variant}_{sample_idx:04d}"


def _output_path(run_dir: Path, variant: str, post_id: str, sample_idx: int) -> Path:
    return run_dir / variant / "output-results" / f"{_sample_label(post_id, variant, sample_idx)}.json"


def _failure_path(run_dir: Path, variant: str, post_id: str, sample_idx: int) -> Path:
    return run_dir / variant / "failures" / f"{_sample_label(post_id, variant, sample_idx)}.json"


def _is_done(run_dir: Path, variant: str, post_id: str, sample_idx: int) -> bool:
    path = _output_path(run_dir, variant, post_id, sample_idx)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(payload)


def _is_settled(
    run_dir: Path, variant: str, post_id: str, sample_idx: int, *, retry_failures: bool
) -> bool:
    if _is_done(run_dir, variant, post_id, sample_idx):
        return True
    if retry_failures:
        return False
    return _failure_path(run_dir, variant, post_id, sample_idx).exists()


def _lane_counts(run_dir: Path, campaign: dict[str, Any]) -> dict[str, dict[str, int]]:
    post_ids = list(campaign["post_ids"])
    out: dict[str, dict[str, int]] = {}
    for variant in campaign["variants"]:
        done = sum(
            1 for idx, post_id in enumerate(post_ids) if _is_done(run_dir, variant, post_id, idx)
        )
        failed = sum(
            1
            for idx, post_id in enumerate(post_ids)
            if (not _is_done(run_dir, variant, post_id, idx))
            and _failure_path(run_dir, variant, post_id, idx).exists()
        )
        remaining = max(0, int(campaign["target_samples"]) - done - failed)
        out[variant] = {
            "done": done,
            "failed": failed,
            "remaining": remaining,
            "target": int(campaign["target_samples"]),
            "attempted": done + failed,
        }
    return out


def _pending_work(
    run_dir: Path,
    campaign: dict[str, Any],
    max_new: int | None,
    *,
    retry_failures: bool,
) -> list[tuple[str, int, str]]:
    """Interleave variants by sample index so early chunks exercise both lanes."""
    work: list[tuple[str, int, str]] = []
    post_ids = list(campaign["post_ids"])
    variants = list(campaign["variants"])
    for idx, post_id in enumerate(post_ids):
        for variant in variants:
            if _is_settled(run_dir, variant, post_id, idx, retry_failures=retry_failures):
                continue
            work.append((variant, idx, post_id))
            if max_new is not None and len(work) >= max_new:
                return work
    return work


def _default_run_id() -> str:
    return f"barb_campaign_500_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"


def _resolve_run_dir(run_id: str, run_dir: Path | None) -> Path:
    resolved = (run_dir or (BARB_RUNS_ROOT / run_id)).resolve()
    if not str(resolved).startswith(str(BARB_RUNS_ROOT.resolve())):
        raise SystemExit(f"BARB runs must stay under {BARB_RUNS_ROOT}, got {resolved}")
    return resolved


def _build_post_plan(
    *,
    angles_dir: Path,
    dataset_dir: Path,
    target: int,
    explicit_post_ids: list[str],
    allow_post_reuse: bool,
) -> list[str]:
    return e2e.select_post_ids(
        explicit_post_ids=explicit_post_ids,
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        samples_per_profile=target,
        allow_post_reuse=allow_post_reuse,
    )[:target]


def _init_or_load_campaign(args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    campaign_path = run_dir / "campaign.json"
    if campaign_path.exists() and not args.overwrite:
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        _LOG.info("campaign_resumed", run_dir=str(run_dir), target=campaign.get("target_samples"))
        return campaign
    if run_dir.exists() and args.overwrite:
        import shutil

        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    post_ids = _build_post_plan(
        angles_dir=Path(args.angles_dir),
        dataset_dir=Path(args.dataset_dir),
        target=args.target,
        explicit_post_ids=list(args.post_id or []),
        allow_post_reuse=bool(args.allow_post_reuse),
    )
    campaign = {
        "schema_version": 1,
        "run_id": args.run_id,
        "created_at_utc": _utc_now(),
        "goal": "Prove BARB > balanced on paired real-post encode/decode quality",
        "target_samples": args.target,
        "variants": list(args.variants),
        "angles_dir": str(Path(args.angles_dir).resolve()),
        "dataset_dir": str(Path(args.dataset_dir).resolve()),
        "context_sampler": args.context_sampler,
        "max_retries": args.max_retries,
        "allow_post_reuse": bool(args.allow_post_reuse),
        "post_ids": post_ids,
        "unique_post_ids": sorted(set(post_ids)),
    }
    _write_json(campaign_path, campaign)
    _write_json(run_dir / "post_ids.json", {"post_ids": post_ids})
    _append_event(run_dir, "campaign_created", target=args.target, posts=len(post_ids))
    return campaign


def _write_status(
    run_dir: Path,
    campaign: dict[str, Any],
    *,
    phase: str,
    current: dict[str, Any] | None = None,
    note: str = "",
) -> None:
    lanes = _lane_counts(run_dir, campaign)
    total_done = sum(v["done"] for v in lanes.values())
    total_failed = sum(v["failed"] for v in lanes.values())
    total_target = sum(v["target"] for v in lanes.values())
    progress = {
        "updated_at_utc": _utc_now(),
        "run_id": campaign["run_id"],
        "phase": phase,
        "goal": campaign["goal"],
        "target_samples_per_variant": campaign["target_samples"],
        "total_done": total_done,
        "total_failed": total_failed,
        "total_target": total_target,
        "pct_complete": round(100.0 * total_done / total_target, 2) if total_target else 0.0,
        "lanes": lanes,
        "current": current or {},
        "note": note,
        "artifacts": {
            "campaign": str((run_dir / "campaign.json").resolve()),
            "progress": str((run_dir / "progress.json").resolve()),
            "heartbeat": str((run_dir / "heartbeat.json").resolve()),
            "status": str((run_dir / "status.txt").resolve()),
            "events": str((run_dir / "events.jsonl").resolve()),
        },
    }
    heartbeat = {
        "updated_at_utc": progress["updated_at_utc"],
        "phase": phase,
        "pid": os.getpid(),
        "current": current or {},
        "pct_complete": progress["pct_complete"],
        "alive": True,
    }
    _write_json(run_dir / "progress.json", progress)
    _write_json(run_dir / "heartbeat.json", heartbeat)
    lines = [
        f"BARB campaign {campaign['run_id']}",
        f"phase: {phase}",
        (
            f"progress: {total_done}/{total_target} ok "
            f"({progress['pct_complete']}%), failed={total_failed}"
        ),
        f"updated: {progress['updated_at_utc']}",
    ]
    for name, lane in lanes.items():
        lines.append(
            f"  {name}: {lane['done']}/{lane['target']} ok, "
            f"failed={lane['failed']}, left={lane['remaining']}"
        )
    if current:
        lines.append(
            "current: "
            f"variant={current.get('variant')} post={current.get('post_id')} "
            f"idx={current.get('sample_index')}"
        )
    if note:
        lines.append(f"note: {note}")
    (run_dir / "status.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_one(
    *,
    run_dir: Path,
    campaign: dict[str, Any],
    variant_name: str,
    sample_idx: int,
    post_id: str,
    max_retries: int,
    stego: StegoPipeline,
    receiver: ReceiverPipeline,
) -> dict[str, Any]:
    variant = resolve_experiment_variants([variant_name])[0]
    profile_dir = run_dir / variant.name
    for sub in ("input-angles", "dataset", "output-results", "failures", "metrics"):
        (profile_dir / sub).mkdir(parents=True, exist_ok=True)
    with applied_experiment_variant(
        variant,
        force_model_generation=True,
        default_secret="barb-campaign-security-profile-secret",
    ):
        try:
            entry = e2e._run_sample(
                run_id=str(campaign["run_id"]),
                variant=variant,
                post_id=post_id,
                sample_idx=sample_idx,
                angles_dir=Path(campaign["angles_dir"]),
                dataset_dir=Path(campaign["dataset_dir"]),
                input_dir=profile_dir / "input-angles",
                profile_dataset_dir=profile_dir / "dataset",
                output_dir=profile_dir / "output-results",
                stego=stego,
                receiver=receiver,
                max_retries=max_retries,
                skip_receiver_decode=False,
                max_transient_sample_retries=e2e.DEFAULT_MAX_TRANSIENT_SAMPLE_RETRIES,
                transient_sample_retry_base_delay_seconds=(
                    e2e.DEFAULT_TRANSIENT_SAMPLE_RETRY_BASE_DELAY_SECONDS
                ),
            )
            return {"ok": True, "entry": entry}
        except Exception as exc:
            sample_label = _sample_label(post_id, variant.name, sample_idx)
            failure = {
                "variant": variant.name,
                "post_id": post_id,
                "sample_index": sample_idx,
                "error": f"{type(exc).__name__}: {exc}",
                "ts": _utc_now(),
            }
            e2e._write_json(profile_dir / "failures" / f"{sample_label}.json", failure)
            return {"ok": False, "failure": failure}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=_default_run_id())
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--target", type=int, default=500, help="Samples per variant.")
    parser.add_argument(
        "--max-new-samples",
        type=int,
        default=None,
        help="Process at most N pending encodes this invocation (smoke/chunking).",
    )
    parser.add_argument(
        "--angles-dir",
        type=Path,
        default=(
            _REPO_ROOT
            / "datasets"
            / "prep_runs"
            / "context_weighted_v2"
            / "scale300_20260729"
            / "news_angles"
        ),
    )
    parser.add_argument(
        "--dataset-dir", type=Path, default=_REPO_ROOT / "datasets" / "news_cleaned"
    )
    parser.add_argument("--post-id", action="append", default=[])
    parser.add_argument("--variant", dest="variants", action="append", default=None)
    parser.add_argument("--context-sampler", default="post_level_v1")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--allow-post-reuse", action="store_true", default=True)
    parser.add_argument("--no-allow-post-reuse", action="store_false", dest="allow_post_reuse")
    parser.add_argument(
        "--retry-failures",
        action="store_true",
        help="Re-attempt samples that already have a failure artifact.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.variants:
        args.variants = list(DEFAULT_VARIANTS)
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    run_dir = _resolve_run_dir(args.run_id, args.run_dir)
    args.run_id = run_dir.name if args.run_dir else args.run_id

    with override_workflow_context_sampler(args.context_sampler):
        campaign = _init_or_load_campaign(args, run_dir)
        work = _pending_work(
            run_dir,
            campaign,
            args.max_new_samples,
            retry_failures=bool(args.retry_failures),
        )
        _write_status(
            run_dir,
            campaign,
            phase="starting",
            note=f"queued={len(work)} max_new={args.max_new_samples}",
        )
        _append_event(run_dir, "run_start", queued=len(work), pid=os.getpid())
        if not work:
            _write_status(run_dir, campaign, phase="complete", note="nothing pending")
            _append_event(run_dir, "run_complete", reason="nothing_pending")
            print(f"BARB campaign complete (nothing pending): {run_dir}")
            return 0

        stego, receiver = StegoPipeline(), ReceiverPipeline()
        succeeded = failed = 0
        for variant_name, sample_idx, post_id in work:
            current = {
                "variant": variant_name,
                "sample_index": sample_idx,
                "post_id": post_id,
            }
            _write_status(run_dir, campaign, phase="encoding", current=current)
            _append_event(run_dir, "sample_start", **current)
            t0 = time.perf_counter()
            result = _run_one(
                run_dir=run_dir,
                campaign=campaign,
                variant_name=variant_name,
                sample_idx=sample_idx,
                post_id=post_id,
                max_retries=args.max_retries,
                stego=stego,
                receiver=receiver,
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if result["ok"]:
                succeeded += 1
                entry = result["entry"]
                _append_event(
                    run_dir,
                    "sample_ok",
                    **current,
                    elapsed_ms=elapsed_ms,
                    receiver_ok=bool((entry.get("receiver_decode") or {}).get("succeeded")),
                )
            else:
                failed += 1
                _append_event(
                    run_dir,
                    "sample_fail",
                    **current,
                    elapsed_ms=elapsed_ms,
                    error=(result.get("failure") or {}).get("error"),
                )
            _write_status(
                run_dir,
                campaign,
                phase="encoding",
                current=current,
                note=f"batch_ok={succeeded} batch_fail={failed}",
            )

        lanes = _lane_counts(run_dir, campaign)
        done = all(lane["remaining"] == 0 for lane in lanes.values())
        phase = "complete" if done else "paused"
        _write_status(
            run_dir,
            campaign,
            phase=phase,
            note=f"invocation ok={succeeded} fail={failed}",
        )
        _append_event(
            run_dir,
            "run_complete",
            phase=phase,
            succeeded=succeeded,
            failed=failed,
            lanes=lanes,
        )
        print(f"BARB campaign {phase}: {run_dir}")
        print(f"  this invocation: ok={succeeded} fail={failed}")
        for name, lane in lanes.items():
            print(f"  {name}: {lane['done']}/{lane['target']}")
        return 0 if failed == 0 or succeeded > 0 else 1


if __name__ == "__main__":
    append_current_pid_to_log()
    raise SystemExit(main())
