#!/usr/bin/env python3
"""Print BARB campaign progress for humans or scheduled watchers."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
BARB_RUNS_ROOT = _REPO_ROOT / "metrics" / "experiments" / "barb" / "runs"


def _latest_run() -> Path | None:
    if not BARB_RUNS_ROOT.exists():
        return None
    runs = [p for p in BARB_RUNS_ROOT.iterdir() if p.is_dir() and (p / "campaign.json").exists()]
    if not runs:
        return None
    return max(runs, key=lambda p: p.stat().st_mtime)


def _age_seconds(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(UTC) - then.astimezone(UTC)).total_seconds())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    run_dir = (args.run_dir or _latest_run() or Path()).resolve()
    if not run_dir.exists() or not (run_dir / "campaign.json").exists():
        print(f"No BARB campaign found under {BARB_RUNS_ROOT}", file=sys.stderr)
        return 2
    progress = {}
    heartbeat = {}
    if (run_dir / "progress.json").exists():
        progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    if (run_dir / "heartbeat.json").exists():
        heartbeat = json.loads((run_dir / "heartbeat.json").read_text(encoding="utf-8"))
    status_txt = (
        (run_dir / "status.txt").read_text(encoding="utf-8")
        if (run_dir / "status.txt").exists()
        else ""
    )
    age = _age_seconds(heartbeat.get("updated_at_utc") or progress.get("updated_at_utc"))
    stale = age is not None and age > 45 * 60
    report = {
        "run_dir": str(run_dir),
        "phase": progress.get("phase") or heartbeat.get("phase"),
        "pct_complete": progress.get("pct_complete"),
        "total_done": progress.get("total_done"),
        "total_target": progress.get("total_target"),
        "lanes": progress.get("lanes"),
        "current": progress.get("current") or heartbeat.get("current"),
        "heartbeat_age_seconds": age,
        "stale": stale,
        "status_txt": status_txt.strip(),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(status_txt or json.dumps(report, indent=2))
        if stale:
            print(f"WARNING: heartbeat stale ({int(age or 0)}s old)", file=sys.stderr)
            return 1
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
