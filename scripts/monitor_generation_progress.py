from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = REPO_ROOT / "metrics" / "e2e_runs"
LOGS_ROOT = REPO_ROOT / "metrics" / "automation_logs"
EVENTS_LOG = LOGS_ROOT / "generation_monitor_events.jsonl"


def _write_event(action: str, **kwargs: object) -> None:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"ts": datetime.now(UTC).isoformat(), "action": action, **kwargs}
    with EVENTS_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON in {path}")
    return payload


def _latest_actual_run() -> Path | None:
    runs = sorted(
        [path for path in RUNS_ROOT.glob("actual_workload_e2e_*") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
    )
    return runs[-1] if runs else None


def _sum_lanes(lanes: list[dict[str, Any]], key: str) -> int:
    return sum(int(lane.get(key) or 0) for lane in lanes)


def _restart_from_progress(run_dir: Path, progress: dict[str, Any]) -> None:
    target = int(progress.get("target_successful_samples_per_lane") or 25)
    lanes = progress.get("lanes")
    lane_list = lanes if isinstance(lanes, list) else []
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/run_actual_workload_e2e.py",
        "--samples-per-profile",
        str(target),
        "--max-retries",
        "1",
        "--run-dir",
        str(run_dir),
        "--overwrite",
        "--log-level",
        "INFO",
    ]
    for lane in lane_list:
        lane_id = lane.get("lane_id")
        if isinstance(lane_id, str) and lane_id:
            cmd.extend(["--variant", lane_id])
    subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    _write_event("restart_triggered", command=cmd, run_dir=str(run_dir))


def main() -> None:
    run_dir = _latest_actual_run()
    if run_dir is None:
        _write_event("no_run_found")
        return
    progress_path = run_dir / "progress.json"
    if not progress_path.is_file():
        _write_event("missing_progress", run_dir=str(run_dir))
        return
    progress = _read_json(progress_path)
    lanes = progress.get("lanes")
    lane_list = [lane for lane in lanes if isinstance(lane, dict)] if isinstance(lanes, list) else []
    infra = _sum_lanes(lane_list, "infrastructure_failures")
    generation = _sum_lanes(lane_list, "generation_failures")
    decode = _sum_lanes(lane_list, "decode_failures")
    metric = _sum_lanes(lane_list, "metric_failures")
    data = _sum_lanes(lane_list, "data_failures")
    judge = _sum_lanes(lane_list, "judge_failures")
    attempted = _sum_lanes(lane_list, "requested")
    infra_rate = (infra / attempted) if attempted > 0 else 0.0

    _write_event(
        "heartbeat",
        run_dir=str(run_dir),
        status=progress.get("status"),
        stage=progress.get("stage"),
        infra=infra,
        generation=generation,
        decode=decode,
        metric=metric,
        data=data,
        judge=judge,
        infra_rate=infra_rate,
    )
    if infra_rate > 0.10:
        _write_event("blocked_infra_rate", run_dir=str(run_dir), infra_rate=infra_rate)
        return
    if infra > 0 and generation == 0 and decode == 0 and data == 0:
        _restart_from_progress(run_dir, progress)


if __name__ == "__main__":
    main()
