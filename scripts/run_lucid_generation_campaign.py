#!/usr/bin/env python3
"""Continuously drain fresh LUCID prep queues for a duration or indefinitely."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from event_loop_manager import start_event_loop, stop_event_loop  # noqa: E402
from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from workflows.runner import WorkflowRunner  # noqa: E402
from workflows.stages import ANGLES_STEP, DATA_LOAD_STEP, RESEARCH_STEP  # noqa: E402
from workflows.utils.prep_run_manifest import write_prep_run_manifest  # noqa: E402

_LOG = logger.bind(component="LucidGenerationCampaign")
_DEFAULT_ROOT = _REPO_ROOT / "datasets" / "prep_runs" / "LUCID" / "tangents_db_v1_fresh"


class CampaignState(BaseModel):
    """Durable progress and heartbeat for one timed generation campaign."""

    status: str = "starting"
    pid: int = 0
    started_at_utc: str = ""
    deadline_utc: str = ""
    updated_at_utc: str = ""
    iterations: int = 0
    offsets: dict[str, int] = Field(default_factory=dict)
    totals: dict[str, int] = Field(default_factory=dict)
    attempts: dict[str, int] = Field(default_factory=dict)
    last_batch: dict[str, Any] = Field(default_factory=dict)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_json(path: Path) -> int:
    return sum(1 for item in path.glob("*.json") if item.is_file()) if path.is_dir() else 0


def _totals(root: Path) -> dict[str, int]:
    return {
        "news_url_fetched": _count_json(root / "news_url_fetched"),
        "news_researched": _count_json(root / "news_researched"),
        "news_angles": _count_json(root / "news_angles"),
    }


def _write_state(path: Path, state: CampaignState, root: Path) -> None:
    state.updated_at_utc = _utc_now()
    state.totals = _totals(root)
    path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _pending_names(
    runner: WorkflowRunner, step: str, count: int, offset: int
) -> list[str]:
    try:
        listing = runner.backend.posts_list(step=step, count=count, offset=offset)
    except ValueError:
        return []
    return [str(name) for name in listing.get("fileNames", [])]


def _load_posts(runner: WorkflowRunner, step: str, names: list[str]) -> list[dict[str, Any]]:
    return [runner.backend.get_post_local(name, step) for name in names]


def _run_angles(
    runner: WorkflowRunner, names: list[str]
) -> tuple[int, list[dict[str, Any]]]:
    posts = _load_posts(runner, ANGLES_STEP, names)
    results = runner.gen_angles.process_post_objects(posts=posts, step=ANGLES_STEP)
    return len(names) - len(results), results


def _run_research(
    runner: WorkflowRunner, names: list[str]
) -> tuple[int, list[dict[str, Any]]]:
    posts = _load_posts(runner, RESEARCH_STEP, names)
    results = runner.research.process_post_objects(
        posts=posts, step=RESEARCH_STEP, disable_bing_fallback=False
    )
    return len(names) - len(results), results


def _record_batch(
    state: CampaignState, stage: str, attempted: int, succeeded: int, failed: int
) -> None:
    state.iterations += 1
    state.attempts[stage] = state.attempts.get(stage, 0) + attempted
    state.last_batch = {
        "stage": stage,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "finished_at_utc": _utc_now(),
    }


def _run_object_stage(
    runner: WorkflowRunner,
    state: CampaignState,
    batch_count: int,
    label: str,
    step: str,
    run: Any,
) -> bool:
    offset = state.offsets.get(label, 0)
    names = _pending_names(runner, step, batch_count, offset)
    if not names:
        return False
    failed, results = run(runner, names)
    state.offsets[label] = offset + failed
    _record_batch(state, label, len(names), len(results), failed)
    return True


def _run_data_load(
    runner: WorkflowRunner, state: CampaignState, batch_count: int, batch_size: int
) -> bool:
    offset = state.offsets.get("data_load", 0)
    names = _pending_names(runner, DATA_LOAD_STEP, batch_count, offset)
    if not names:
        return False
    results = runner.run_data_load(count=len(names), offset=offset, batch_size=batch_size)
    failed = len(names) - len(results)
    state.offsets["data_load"] = offset + failed
    _record_batch(state, "data_load", len(names), len(results), failed)
    return True


def _apply_env(root: Path, llm_backend: str) -> None:
    os.environ["WORKFLOW_DATASET_ROOT"] = str(root)
    os.environ["WORKFLOW_DATASET_SEED_GLOBAL"] = "1"
    os.environ["WORKFLOW_TANGENT_DB_BUILDER"] = "lucid"
    os.environ["WORKFLOW_URL_FETCH_JINA_FIRST"] = "1"
    os.environ.setdefault("WORKFLOW_RESEARCH_FETCH_CONCURRENCY", "1")
    os.environ.setdefault("WORKFLOW_CONTEXT_SAMPLER", "context_weighted_v2")
    os.environ["WORKFLOW_LLM_BACKEND"] = llm_backend


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default=str(_DEFAULT_ROOT))
    parser.add_argument("--duration-hours", type=float, default=4.0)
    parser.add_argument(
        "--forever",
        action="store_true",
        help="Run until explicitly stopped; ignores --duration-hours.",
    )
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--data-load-batch-size", type=int, default=5)
    parser.add_argument("--llm-backend", default="ai_studio")
    parser.add_argument(
        "--stage-mode",
        choices=("full", "data_load", "research", "angles"),
        default="full",
    )
    parser.add_argument("--initial-offset", type=int, default=0)
    parser.add_argument("--failure-cooldown-seconds", type=float, default=60.0)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def _new_state(
    duration_hours: float, stage_mode: str, initial_offset: int, *, forever: bool = False
) -> CampaignState:
    now = datetime.now(UTC)
    deadline = None if forever else now + timedelta(hours=max(4.0, duration_hours))
    offsets = {"data_load": 0, "research": 0, "angles": 0}
    if stage_mode in offsets:
        offsets[stage_mode] = max(0, initial_offset)
    return CampaignState(
        status="running",
        pid=os.getpid(),
        started_at_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        deadline_utc=deadline.strftime("%Y-%m-%dT%H:%M:%SZ") if deadline else "",
        offsets=offsets,
    )


def _campaign_loop(
    runner: WorkflowRunner,
    root: Path,
    state_path: Path,
    state: CampaignState,
    args: argparse.Namespace,
) -> None:
    deadline = (
        datetime.fromisoformat(state.deadline_utc.replace("Z", "+00:00"))
        if state.deadline_utc
        else None
    )
    while deadline is None or datetime.now(UTC) < deadline:
        batch_count = max(1, args.batch_count)
        worked = False
        if args.stage_mode in {"full", "angles"}:
            worked = _run_object_stage(
                runner, state, batch_count, "angles", ANGLES_STEP, _run_angles
            )
        if not worked and args.stage_mode in {"full", "research"}:
            worked = _run_object_stage(
                runner, state, batch_count, "research", RESEARCH_STEP, _run_research
            )
        if not worked and args.stage_mode in {"full", "research", "data_load"}:
            worked = _run_data_load(
                runner, state, batch_count, max(1, args.data_load_batch_size)
            )
        if not worked:
            state.offsets = {"data_load": 0, "research": 0, "angles": 0}
            if args.stage_mode in state.offsets:
                state.offsets[args.stage_mode] = max(0, args.initial_offset)
            time.sleep(30)
        _write_state(state_path, state, root)
        if state.last_batch.get("stage") == "angles" and state.last_batch.get("failed"):
            time.sleep(max(1.0, float(args.failure_cooldown_seconds)))


def main() -> int:
    args = _parse_args()
    root = Path(args.dataset_root).resolve()
    service = root / "service"
    service.mkdir(parents=True, exist_ok=True)
    _apply_env(root, str(args.llm_backend))
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    suffix = "" if args.stage_mode == "full" else f"_{args.stage_mode}"
    state_path = service / f"campaign{suffix}_state.json"
    state = _new_state(
        float(args.duration_hours),
        str(args.stage_mode),
        int(args.initial_offset),
        forever=bool(args.forever),
    )
    (service / f"campaign{suffix}.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    notes = (
        "Persistent fresh LUCID generation campaign"
        if args.forever
        else "Timed fresh LUCID generation campaign"
    )
    write_prep_run_manifest(run_id=root.name, notes=notes)
    _write_state(state_path, state, root)
    _LOG.info("lucid_generation_campaign_start", state=state.model_dump(mode="json"))
    start_event_loop()
    try:
        _campaign_loop(WorkflowRunner(), root, state_path, state, args)
        state.status = "completed"
    except Exception:
        state.status = "failed"
        _LOG.exception("lucid_generation_campaign_failed")
        raise
    finally:
        _write_state(state_path, state, root)
        stop_event_loop()
    _LOG.info("lucid_generation_campaign_complete", totals=state.totals)
    return 0


if __name__ == "__main__":
    append_current_pid_to_log()
    raise SystemExit(main())
