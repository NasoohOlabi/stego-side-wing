#!/usr/bin/env python3
"""After LUCID 100more generation: combine summaries, ZLG compare, metrics, Codex judge.

Phases (GPU-sequential):
  1. Wait until campaign has >= target succeeded outputs (optional --wait)
  2. Write a combined source summary for run_zlg_batch_comparison
  3. Require ZLG :9000 healthy (caller must unload LM Studio / start llama+API)
  4. run_zlg_batch_comparison (capacity_matched)
  5. build_zlg_method_comparison_dataset
  6. run_codex_judge_campaign (auto)

Does not unload LM Studio or start ZLG itself.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

_REPO = Path(__file__).resolve().parent.parent
_CAMPAIGN = "LUCID_context_weighted_v2_balanced_100more"
_RUNS = _REPO / "metrics" / "e2e_runs"
_ZLG_RUN = _REPO / "metrics" / "zlg_comparison_runs" / "zlg_lucid_100more"
_ANGLES = _REPO / "datasets" / "prep_runs" / "LUCID" / "context_weighted_v2" / "news_angles"
_COMBINED = _RUNS / f"{_CAMPAIGN}_combined_summary.json"
_TARGET = 100
_ZLG_URL = "http://127.0.0.1:9000"


def _campaign_dirs() -> list[Path]:
    return sorted(
        p
        for p in _RUNS.glob(f"{_CAMPAIGN}*")
        if p.is_dir() and (p.name == _CAMPAIGN or p.name.startswith(f"{_CAMPAIGN}_"))
    )


def _count_outputs(run_dir: Path) -> int:
    out = run_dir / "balanced" / "output-results"
    if not out.is_dir():
        return 0
    return sum(1 for p in out.glob("*.json") if p.is_file())


def _total_succeeded() -> int:
    return sum(_count_outputs(d) for d in _campaign_dirs())


def _rewrite_output_paths(entry: dict, run_dir: Path) -> dict:
    row = dict(entry)
    of = row.get("output_file")
    if not of:
        return row
    path = Path(str(of))
    if not path.is_absolute():
        path = (run_dir / path).resolve()
    else:
        path = path.resolve()
    row["output_file"] = str(path)
    row["source_run_dir"] = str(run_dir.resolve())
    return row


def _collect_entries() -> list[dict]:
    entries: list[dict] = []
    for run_dir in _campaign_dirs():
        summary_path = run_dir / "summary.json"
        if not summary_path.is_file():
            summary_path = run_dir / "balanced" / "summary.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        direct = summary.get("entries") or []
        if isinstance(direct, list) and direct:
            for e in direct:
                if isinstance(e, dict) and e.get("output_file"):
                    entries.append(_rewrite_output_paths(e, run_dir))
            continue
        for profile in summary.get("profile_summaries") or []:
            if not isinstance(profile, dict):
                continue
            for e in profile.get("entries") or []:
                if isinstance(e, dict) and e.get("output_file"):
                    entries.append(_rewrite_output_paths(e, run_dir))
    return entries


def write_combined_summary() -> Path:
    entries = _collect_entries()
    payload = {
        "campaign": _CAMPAIGN,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "campaign_dirs": [str(d) for d in _campaign_dirs()],
        "total_succeeded_samples": len(entries),
        "entries": entries,
    }
    _COMBINED.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _COMBINED


def _zlg_ready(timeout: float = 5.0) -> bool:
    try:
        with urlopen(f"{_ZLG_URL}/health", timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (URLError, TimeoutError, OSError):
        return False


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=str(_REPO))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", action="store_true", help="Poll until target successes exist")
    parser.add_argument("--target", type=int, default=_TARGET)
    parser.add_argument("--interval-sec", type=int, default=120)
    parser.add_argument("--skip-zlg", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--zlg-threshold", type=float, default=0.01)
    parser.add_argument("--zlg-temperature", type=float, default=0.7)
    parser.add_argument("--zlg-temperature-alpha", type=float, default=1.0)
    parser.add_argument("--zlg-max-bpw", type=int, default=2)
    parser.add_argument("--judge-pilot-limit", type=int, default=50)
    parser.add_argument("--judge-backend", choices=("claude", "codex"), default="claude")
    parser.add_argument("--judge-model", default=None)
    args = parser.parse_args()

    if args.wait:
        while True:
            outs = _total_succeeded()
            entries = _collect_entries()
            print(
                f"waiting outputs={outs}/{args.target} summary_entries={len(entries)}",
                flush=True,
            )
            if outs >= args.target and len(entries) >= args.target:
                break
            time.sleep(max(15, args.interval_sec))

    combined = write_combined_summary()
    n = len(json.loads(combined.read_text(encoding="utf-8"))["entries"])
    print(f"combined_summary={combined} entries={n}", flush=True)
    if n < args.target:
        print(f"ERROR: only {n} entries; need {args.target}", file=sys.stderr)
        return 2

    if not args.skip_zlg:
        if not _zlg_ready():
            print(
                "ERROR: ZLG not healthy at "
                f"{_ZLG_URL}. Unload LM Studio, start ../zero-shot-GLS "
                "start_llama.bat + start_stego_api.bat, then re-run.",
                file=sys.stderr,
            )
            return 3
        _run(
            [
                "uv",
                "run",
                "python",
                "scripts/run_zlg_batch_comparison.py",
                "--source-summary",
                str(combined),
                "--server-url",
                _ZLG_URL,
                "--run-dir",
                str(_ZLG_RUN),
                "--comparison-mode",
                "capacity_matched",
                "--zlg-threshold",
                str(args.zlg_threshold),
                "--zlg-temperature",
                str(args.zlg_temperature),
                "--zlg-temperature-alpha",
                str(args.zlg_temperature_alpha),
                "--zlg-max-bpw",
                str(args.zlg_max_bpw),
                "--max-retries",
                "5",
            ]
        )

    if not args.skip_build:
        _run(
            [
                "uv",
                "run",
                "python",
                "scripts/build_zlg_method_comparison_dataset.py",
                "--zlg-run-dir",
                str(_ZLG_RUN),
                "--source-summary",
                str(combined),
                "--dataset-dir",
                str(_ANGLES),
                "--device",
                "auto",
            ]
        )

    if not args.skip_judge:
        judge_cmd = [
            "uv",
            "run",
            "python",
            "scripts/run_codex_judge_campaign.py",
            "--run-dir",
            str(_ZLG_RUN),
            "--phase",
            "auto",
            "--pilot-limit",
            str(args.judge_pilot_limit),
            "--max-workers",
            "4",
            "--backend",
            args.judge_backend,
        ]
        if args.judge_model is not None:
            judge_cmd.extend(["--model", args.judge_model])
        _run(judge_cmd)

    print("pipeline_complete", _ZLG_RUN, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
