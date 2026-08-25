#!/usr/bin/env python3
"""Watchdog for a 100-success LUCID follow-on campaign (full ZLG+metrics+judge) used for ZLG comparison.

Starts ``run_actual_workload_e2e.py`` when needed, never ``--overwrite``s an
in-progress dir, and opens continuation runs for remaining successes after a
crash or shortfall. Updates workspace ``processes.md`` on every tick.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

_REPO = Path(__file__).resolve().parent.parent
_WORKSPACE = _REPO.parent
_PROCESSES = _WORKSPACE / "processes.md"
_RUNS = _REPO / "metrics" / "e2e_runs"
_ANGLES = _REPO / "datasets" / "prep_runs" / "LUCID" / "context_weighted_v2" / "news_angles"
_DATASET = _REPO / "datasets" / "news_cleaned"
_CAMPAIGN = "LUCID_context_weighted_v2_balanced_100more"
_TARGET = 100
_LM_URL = os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:8081").rstrip("/")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _lm_ready(timeout: float = 3.0) -> bool:
    try:
        with urlopen(f"{_LM_URL}/v1/models", timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (URLError, TimeoutError, OSError):
        return False


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


def _find_python_worker() -> tuple[int | None, str]:
    """Return (pid, cmdline) for an active campaign e2e process, if any."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return _find_python_worker_wmic()
    needle = "run_actual_workload_e2e.py"
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
        except (psutil.Error, TypeError):
            continue
        if needle in cmd and _CAMPAIGN in cmd:
            return int(proc.info["pid"]), cmd
    return None, ""


def _find_python_worker_wmic() -> tuple[int | None, str]:
    try:
        raw = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -match 'run_actual_workload_e2e' "
                f"-and $_.CommandLine -match '{_CAMPAIGN}' }} | "
                "Select-Object -First 1 ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return None, ""
    if not raw:
        return None, ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, ""
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None, ""
    pid = data.get("ProcessId")
    cmd = str(data.get("CommandLine") or "")
    return (int(pid) if pid is not None else None), cmd


def _next_run_dir() -> Path:
    existing = _campaign_dirs()
    if not existing:
        return _RUNS / _CAMPAIGN
    # Prefer reusing empty primary; otherwise open continuation.
    primary = _RUNS / _CAMPAIGN
    if primary.exists() and _count_outputs(primary) == 0 and not (primary / "summary.json").exists():
        return primary
    n = 2
    while True:
        cand = _RUNS / f"{_CAMPAIGN}_cont{n}"
        if not cand.exists():
            return cand
        n += 1


def _backend_env() -> dict[str, str]:
    env = os.environ.copy()
    if _lm_ready():
        env["WORKFLOW_LLM_BACKEND"] = "lm_studio"
        env["LM_STUDIO_URL"] = _LM_URL
    else:
        env["WORKFLOW_LLM_BACKEND"] = "ai_studio"
    env.setdefault("WORKFLOW_CONTEXT_SAMPLER", "context_weighted_v2")
    return env


def _start_batch(remaining: int, run_dir: Path) -> subprocess.Popen[str]:
    env = _backend_env()
    log_path = run_dir.parent / f"{run_dir.name}.watchdog.log"
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/run_actual_workload_e2e.py",
        "--variant",
        "balanced",
        "--samples-per-profile",
        str(remaining),
        "--allow-post-reuse",
        "--context-sampler",
        "context_weighted_v2",
        "--angles-dir",
        str(_ANGLES),
        "--dataset-dir",
        str(_DATASET),
        "--run-dir",
        str(run_dir),
        "--max-retries",
        "1",
        "--log-level",
        "INFO",
    ]
    log_f = log_path.open("a", encoding="utf-8")
    log_f.write(f"\n===== start {_now()} backend={env.get('WORKFLOW_LLM_BACKEND')} =====\n")
    log_f.write(" ".join(cmd) + "\n")
    log_f.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=str(_REPO),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        text=True,
    )
    (run_dir.parent / f"{run_dir.name}.watchdog.pid").write_text(
        f"{proc.pid}\n", encoding="utf-8"
    )
    return proc


def _write_processes(
    *,
    status: str,
    pid: int | None,
    succeeded: int,
    note: str,
    backend: str,
    run_dir: Path | None,
) -> None:
    lines = [
        "# Workspace processes",
        "",
        f"Last checked: {_now()}",
        "",
        "## LUCID 100more campaign (ZLG + metrics + judge)",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Status | **{status}** |",
        f"| PID | {pid if pid is not None else 'n/a'} |",
        f"| Progress | **{succeeded} / {_TARGET}** succeeded outputs |",
        f"| LLM backend | `{backend}` |",
        f"| Active/next run dir | `{run_dir}` |" if run_dir else "| Active/next run dir | n/a |",
        f"| Note | {note} |",
        "",
        "### Campaign dirs",
        "",
    ]
    dirs = _campaign_dirs()
    if not dirs:
        lines.append("_none yet_")
    else:
        lines.append("| Dir | Outputs |")
        lines.append("| --- | ---: |")
        for d in dirs:
            lines.append(f"| `{d.name}` | {_count_outputs(d)} |")
    daemon_pid_path = _RUNS / "watch_lucid_100more_samples.daemon.pid"
    daemon_pid = (
        daemon_pid_path.read_text(encoding="utf-8").strip()
        if daemon_pid_path.is_file()
        else "n/a"
    )
    # Count completed 200-campaign outputs for context.
    prior200 = sum(
        sum(1 for p in (d / "balanced" / "output-results").glob("*.json") if p.is_file())
        for d in _RUNS.glob("LUCID_context_weighted_v2_balanced_200*")
        if d.is_dir() and (
            d.name == "LUCID_context_weighted_v2_balanced_200"
            or d.name.startswith("LUCID_context_weighted_v2_balanced_200_")
        )
    )
    lines.extend(
        [
            "",
            "## Prior campaigns",
            "",
            f"| LUCID 200-sample | **{prior200} / 200** succeeded (complete) |",
            "| Note | This campaign adds 100 more; after generation: ZLG compare → metrics build → Codex judge. |",
            "",
            "## Monitor PIDs",
            "",
            "| Role | PID |",
            "| --- | ---: |",
            f"| Active e2e worker | {pid if pid is not None else 'n/a'} |",
            f"| Watchdog daemon | {daemon_pid} |",
            "",
            "## Watchdog",
            "",
            "- Script: `stego-side-wing/scripts/watch_lucid_100more_samples.py`",
            f"- Prefer LM Studio at `{_LM_URL}`; fall back to Google AI Studio if unreachable.",
            "- Never uses `--overwrite` (continuation dirs for remaining successes).",
            f"- Worker log: `metrics/e2e_runs/{_CAMPAIGN}.watchdog.log`",
            "- After complete: unload LM Studio → start :8090/:9000 → `run_zlg_batch_comparison` → `build_zlg_method_comparison_dataset` → `run_codex_judge_campaign`.",
            "",
        ]
    )
    _PROCESSES.write_text("\n".join(lines), encoding="utf-8")


def tick(*, start_if_needed: bool, allow_ai_studio: bool) -> dict[str, object]:
    succeeded = _total_succeeded()
    pid, cmd = _find_python_worker()
    lm = _lm_ready()
    backend = "lm_studio" if lm else ("ai_studio" if allow_ai_studio else "waiting_for_lm_studio")

    if succeeded >= _TARGET:
        _write_processes(
            status="complete",
            pid=pid,
            succeeded=succeeded,
            note="Target reached.",
            backend=backend,
            run_dir=_campaign_dirs()[-1] if _campaign_dirs() else None,
        )
        return {"status": "complete", "succeeded": succeeded, "pid": pid}

    if pid is not None:
        active = next((d for d in reversed(_campaign_dirs()) if _CAMPAIGN in d.name), None)
        _write_processes(
            status="running",
            pid=pid,
            succeeded=succeeded,
            note=f"Worker alive. cmdline_snip={cmd[:120]}",
            backend=backend,
            run_dir=active,
        )
        return {"status": "running", "succeeded": succeeded, "pid": pid}

    if not start_if_needed:
        _write_processes(
            status="idle",
            pid=None,
            succeeded=succeeded,
            note="No worker; start_if_needed=false.",
            backend=backend,
            run_dir=None,
        )
        return {"status": "idle", "succeeded": succeeded, "pid": None}

    if not lm and not allow_ai_studio:
        _write_processes(
            status="blocked",
            pid=None,
            succeeded=succeeded,
            note=f"LM Studio not reachable at {_LM_URL}; waiting.",
            backend=backend,
            run_dir=None,
        )
        return {"status": "blocked", "succeeded": succeeded, "pid": None}

    remaining = _TARGET - succeeded
    run_dir = _next_run_dir()
    if run_dir.exists():
        has_work = _count_outputs(run_dir) > 0 or (run_dir / "summary.json").exists()
        if has_work:
            # Safety: never clobber; pick a fresh continuation name.
            run_dir = _next_run_dir()
        else:
            # e2e refuses existing dirs without --overwrite; clear empty husk.
            import shutil

            shutil.rmtree(run_dir)
    proc = _start_batch(remaining, run_dir)
    time.sleep(2)
    alive = proc.poll() is None
    _write_processes(
        status="running" if alive else "crashed_on_start",
        pid=proc.pid if alive else None,
        succeeded=succeeded,
        note=(
            f"Started batch remaining={remaining} backend={backend}."
            if alive
            else f"Process exited immediately rc={proc.returncode}."
        ),
        backend=backend,
        run_dir=run_dir,
    )
    return {
        "status": "started" if alive else "crashed_on_start",
        "succeeded": succeeded,
        "pid": proc.pid if alive else None,
        "run_dir": str(run_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Single tick then exit.")
    parser.add_argument(
        "--interval-sec",
        type=int,
        default=120,
        help="Loop interval when not --once (default 120).",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Only report/update processes.md; do not start workers.",
    )
    parser.add_argument(
        "--require-lm-studio",
        action="store_true",
        help="Do not fall back to Google AI Studio.",
    )
    args = parser.parse_args()
    start_if_needed = not args.no_start
    allow_ai = not args.require_lm_studio

    def _stop(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while True:
        result = tick(start_if_needed=start_if_needed, allow_ai_studio=allow_ai)
        print(json.dumps({"ts": _now(), **result}, ensure_ascii=True), flush=True)
        if args.once or result.get("status") == "complete":
            return 0
        time.sleep(max(15, int(args.interval_sec)))


if __name__ == "__main__":
    sys.exit(main())
