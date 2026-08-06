"""Run the five Codex judge metrics as a resumable pilot/full campaign."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METRICS = ("standout", "weak_link", "suspicion", "attribution", "register")


def _invoke(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def _run_metrics(args: argparse.Namespace, limit: int | None) -> int:
    for metric in METRICS:
        command = [
            sys.executable,
            "scripts/run_codex_judge.py",
            "--metric",
            metric,
            "--run-dir",
            args.run_dir,
            "--max-workers",
            str(args.max_workers),
            "--model",
            args.model,
            "--reasoning-effort",
            args.reasoning_effort,
        ]
        if limit is not None:
            command.extend(["--limit", str(limit)])
        if _invoke(command):
            return 1
        if _invoke(
            [
                sys.executable,
                "scripts/score_codex_judgments.py",
                "--metric",
                metric,
                "--run-dir",
                args.run_dir,
            ]
        ):
            return 1
    return 0


def _audit(run_dir: str) -> bool:
    return (
        _invoke([sys.executable, "scripts/audit_codex_judge_pilot.py", "--run-dir", run_dir]) == 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", choices=("pilot", "full", "auto"), default="auto")
    parser.add_argument("--pilot-limit", type=int, default=50)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="high")
    args = parser.parse_args()
    if args.phase in {"pilot", "auto"} and _run_metrics(args, args.pilot_limit):
        return 1
    if args.phase == "pilot":
        return 0 if _audit(args.run_dir) else 2
    if not _audit(args.run_dir):
        return 2
    return _run_metrics(args, None)


if __name__ == "__main__":
    raise SystemExit(main())
