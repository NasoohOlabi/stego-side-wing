#!/usr/bin/env python3
"""Durable LUCID prep service: data-load -> research -> gen-angles, then sleep.

Builds a *fresh* TangentsDB-v1 corpus under an isolated ``WORKFLOW_DATASET_ROOT``.
It does **not** run stego encode. Search order per query: Google CSE, then
DuckDuckGo, then Bing (ScrapingDog). When search quota is exhausted (or the seed
corpus is empty), the service sleeps ``--sleep-hours`` (default 24) and starts
another cycle.

Stop gracefully by creating ``<dataset-root>/service/stop.requested`` or sending
SIGINT/SIGTERM. See ``docs/operations/lucid-fresh-research-service.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loguru import logger  # noqa: E402

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from workflows.runner import WorkflowRunner  # noqa: E402
from workflows.utils.prep_run_manifest import write_prep_run_manifest  # noqa: E402

DEFAULT_ROOT = (
    _REPO_ROOT / "datasets" / "prep_runs" / "LUCID" / "tangents_db_v1_fresh"
)
_LOG = logger.bind(component="LucidFreshResearchService")
_STOP = False


class ServiceState(BaseModel):
    """Persisted service heartbeat / cycle accounting."""

    schema_version: int = 1
    status: str = "starting"
    pid: int | None = None
    dataset_root: str = ""
    cycle: int = 0
    started_at_utc: str = ""
    updated_at_utc: str = ""
    last_cycle_started_at_utc: str | None = None
    last_cycle_finished_at_utc: str | None = None
    last_stop_reason: str | None = None
    last_quota_detected: bool = False
    totals: dict[str, int] = Field(default_factory=dict)
    last_prep_result: dict[str, Any] = Field(default_factory=dict)
    sleeping_until_utc: str | None = None
    notes: str = (
        "Fresh LUCID TangentsDB-v1 research-to-angles service; does not encode stego."
    )


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _service_dir(root: Path) -> Path:
    path = root / "service"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(root: Path) -> Path:
    return _service_dir(root) / "state.json"


def _stop_path(root: Path) -> Path:
    return _service_dir(root) / "stop.requested"


def _pid_path(root: Path) -> Path:
    return _service_dir(root) / "worker.pid"


def _write_state(root: Path, state: ServiceState) -> None:
    state.updated_at_utc = _utc_now()
    path = _state_path(root)
    path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _count_json_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.glob("*.json") if path.is_file())


def _corpus_counts(root: Path) -> dict[str, int]:
    return {
        "news_url_fetched": _count_json_files(root / "news_url_fetched"),
        "news_researched": _count_json_files(root / "news_researched"),
        "news_angles": _count_json_files(root / "news_angles"),
    }


def _request_stop(*_args: object) -> None:
    global _STOP
    _STOP = True
    _LOG.warning("lucid_fresh_research_stop_signal")


def _stop_requested(root: Path) -> bool:
    return _STOP or _stop_path(root).exists()


def _sleep_with_stop_checks(root: Path, *, seconds: float, state: ServiceState) -> bool:
    """Sleep up to ``seconds``; return True if stop was requested during the wait."""
    deadline = time.time() + max(0.0, seconds)
    state.sleeping_until_utc = datetime.fromtimestamp(deadline, tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    state.status = "sleeping"
    _write_state(root, state)
    while time.time() < deadline:
        if _stop_requested(root):
            return True
        time.sleep(min(30.0, max(1.0, deadline - time.time())))
    state.sleeping_until_utc = None
    return _stop_requested(root)


def _apply_runtime_env(root: Path) -> None:
    os.environ["WORKFLOW_DATASET_ROOT"] = str(root.resolve())
    os.environ["WORKFLOW_TANGENT_DB_BUILDER"] = "lucid"
    os.environ.setdefault("WORKFLOW_CONTEXT_SAMPLER", "context_weighted_v2")
    os.environ.setdefault("WORKFLOW_DATASET_SEED_GLOBAL", "1")


def _log_progress(event: str, payload: dict[str, Any]) -> None:
    _LOG.info("lucid_fresh_research_progress", progress_event=event, progress_payload=payload)


def _run_one_cycle(
    *,
    root: Path,
    state: ServiceState,
    batch_count: int,
    batch_size: int,
    use_search_fallbacks: bool,
) -> dict[str, Any]:
    state.cycle += 1
    state.status = "running_prep"
    state.last_cycle_started_at_utc = _utc_now()
    state.totals = _corpus_counts(root)
    _write_state(root, state)
    runner = WorkflowRunner()
    result = runner.run_prep_until_search_quota(
        batch_count=batch_count,
        batch_size=batch_size,
        on_progress=_log_progress,
        use_search_fallbacks=use_search_fallbacks,
    )
    prep = result.get("prep") if isinstance(result.get("prep"), dict) else {}
    state.last_prep_result = result
    state.last_stop_reason = str(prep.get("stop_reason") or "")
    state.last_quota_detected = bool(prep.get("quota_detected"))
    state.last_cycle_finished_at_utc = _utc_now()
    state.totals = _corpus_counts(root)
    state.status = "cycle_complete"
    _write_state(root, state)
    _LOG.info(
        "lucid_fresh_research_cycle_complete",
        cycle=state.cycle,
        stop_reason=state.last_stop_reason,
        quota_detected=state.last_quota_detected,
        totals=state.totals,
    )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root",
        default=str(DEFAULT_ROOT),
        help="Isolated prep root (default: datasets/prep_runs/LUCID/tangents_db_v1_fresh).",
    )
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--sleep-hours", type=float, default=24.0)
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Stop after N cycles (0 = run forever until stop file/signal).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single prep cycle and exit (no 24h sleep).",
    )
    parser.add_argument(
        "--no-search-fallbacks",
        action="store_true",
        help="Google only; stop the cycle on first Google quota (no DDG/Bing).",
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--notes",
        default="LUCID TangentsDB-v1 fresh research→angles corpus",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = Path(args.dataset_root)
    if not root.is_absolute():
        root = (_REPO_ROOT / root).resolve()
    else:
        root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    _apply_runtime_env(root)
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)

    service = _service_dir(root)
    stop = _stop_path(root)
    if stop.exists():
        stop.unlink()
    _pid_path(root).write_text(f"{os.getpid()}\n", encoding="utf-8")
    write_prep_run_manifest(run_id=root.name, notes=args.notes)

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    state = ServiceState(
        status="starting",
        pid=os.getpid(),
        dataset_root=str(root),
        started_at_utc=_utc_now(),
        totals=_corpus_counts(root),
    )
    _write_state(root, state)
    _LOG.info(
        "lucid_fresh_research_service_start",
        dataset_root=str(root),
        sleep_hours=args.sleep_hours,
        batch_count=args.batch_count,
        batch_size=args.batch_size,
        use_search_fallbacks=not args.no_search_fallbacks,
        service_dir=str(service),
    )

    while not _stop_requested(root):
        if args.max_cycles and state.cycle >= args.max_cycles:
            break
        _run_one_cycle(
            root=root,
            state=state,
            batch_count=args.batch_count,
            batch_size=args.batch_size,
            use_search_fallbacks=not args.no_search_fallbacks,
        )
        if args.once or _stop_requested(root):
            break
        if args.max_cycles and state.cycle >= args.max_cycles:
            break
        slept_stop = _sleep_with_stop_checks(
            root, seconds=float(args.sleep_hours) * 3600.0, state=state
        )
        if slept_stop:
            break

    state.status = "stopped"
    state.sleeping_until_utc = None
    state.totals = _corpus_counts(root)
    _write_state(root, state)
    if stop.exists():
        stop.unlink()
    _LOG.info("lucid_fresh_research_service_stopped", totals=state.totals, cycle=state.cycle)
    sys.stdout.write(json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    append_current_pid_to_log()
    raise SystemExit(main())
