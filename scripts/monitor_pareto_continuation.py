from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "metrics" / "automation_logs"
RUNS_DIR = REPO_ROOT / "metrics" / "e2e_runs"
EVENTS_LOG = LOGS_DIR / "pareto_monitor_events.jsonl"
PREFIX = "pareto_security_retry_rating_cont_"
STALL_SECONDS = 30 * 60
VARIANTS = [
    "security_legacy",
    "sec_v2_anchored",
    "sec_v2_guided_natural",
    "sec_v2_natural_then_anchor_retry",
    "sec_v2_guided_natural_hybrid_extract",
    "sec_v2_natural_then_anchor_retry_hybrid_extract",
]


def _parse_env_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _read_dotenv_value(name: str) -> str | None:
    env_path = REPO_ROOT / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    prefix = f"{name}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip().strip('"').strip("'")
    return None


def _preferred_google_key_env() -> dict[str, str]:
    keys = _parse_env_list(os.environ.get("GOOGLE_AI_API_KEYS"))
    if not keys:
        keys = _parse_env_list(_read_dotenv_value("GOOGLE_AI_API_KEYS"))
    if len(keys) > 1:
        return {"GOOGLE_AI_API_KEYS": ",".join(keys[1:]), "GOOGLE_AI_API_KEY": keys[1]}
    if keys:
        return {"GOOGLE_AI_API_KEYS": keys[0], "GOOGLE_AI_API_KEY": keys[0]}
    fallback = os.environ.get("GOOGLE_AI_API_KEY") or _read_dotenv_value("GOOGLE_AI_API_KEY")
    return {"GOOGLE_AI_API_KEY": fallback} if fallback else {}


def _now() -> datetime:
    return datetime.now(UTC)


def _event(action: str, **kwargs: object) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"ts": _now().isoformat(), "action": action, **kwargs}
    with EVENTS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _latest_progress_log() -> Path | None:
    logs = sorted(LOGS_DIR.glob(f"{PREFIX}*.progress.jsonl"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def _run_dir_from_log(log_path: Path) -> Path:
    name = log_path.name
    ts = name.removeprefix(PREFIX).removesuffix(".progress.jsonl")
    return RUNS_DIR / f"{PREFIX}{ts}"


def _has_summary(run_dir: Path) -> bool:
    return (run_dir / "summary.json").is_file()


def _read_summary(run_dir: Path) -> dict[str, object] | None:
    try:
        payload = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _summary_is_failed(summary: dict[str, object]) -> bool:
    succeeded = summary.get("total_succeeded_samples")
    failed = summary.get("total_failed_samples")
    return succeeded == 0 and isinstance(failed, int) and failed > 0


def _stale(path: Path, now_ts: float) -> bool:
    try:
        return (now_ts - path.stat().st_mtime) > STALL_SECONDS
    except FileNotFoundError:
        return True


def _stderr_has_fatal(stderr_path: Path) -> bool:
    if not stderr_path.is_file():
        return False
    try:
        tail = stderr_path.read_text(encoding="utf-8", errors="ignore")[-20000:]
    except OSError:
        return False
    markers = ("Traceback (most recent call last)", "FileExistsError", "RuntimeError", "ValueError")
    return any(m in tail for m in markers)


def _start_new_run(reason: str) -> None:
    ts = _now().strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / f"{PREFIX}{ts}"
    progress = LOGS_DIR / f"{PREFIX}{ts}.progress.jsonl"
    stderr = LOGS_DIR / f"{PREFIX}{ts}.stderr.log"
    args = [
        "uv",
        "run",
        "python",
        "scripts/run_actual_workload_e2e.py",
        "--profile",
        "security",
    ]
    for v in VARIANTS:
        args.extend(["--variant", v])
    args.extend([
        "--samples-per-profile",
        "50",
        "--max-retries",
        "1",
        "--run-dir",
        str(run_dir),
        "--progress-log",
        str(progress),
        "--log-level",
        "INFO",
    ])
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out = progress.open("a", encoding="utf-8")
    err = stderr.open("a", encoding="utf-8")
    subprocess.Popen(
        args,
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "WORKFLOW_LLM_BACKEND": "ai_studio",
            **_preferred_google_key_env(),
        },
        stdout=out,
        stderr=err,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    _event("restart", reason=reason, run_dir=str(run_dir), progress_log=str(progress), stderr_log=str(stderr))


def main() -> None:
    now = time.time()
    latest = _latest_progress_log()
    if latest is None:
        _event("no_runs_found")
        _start_new_run("no_progress_logs")
        return

    run_dir = _run_dir_from_log(latest)
    stderr = latest.with_suffix("").with_suffix(".stderr.log")
    if _has_summary(run_dir):
        summary = _read_summary(run_dir)
        if summary is not None and _summary_is_failed(summary):
            _start_new_run("completed_with_zero_success")
            return
        _event("latest_completed", run_dir=str(run_dir))
        return

    is_stalled = _stale(latest, now)
    has_fatal = _stderr_has_fatal(stderr)
    # Fatal markers may be historical in long logs; only restart when stalled.
    if is_stalled:
        reason = "stalled_with_fatal" if has_fatal else "stalled"
        _start_new_run(reason)
        return

    _event("healthy", run_dir=str(run_dir), progress_log=str(latest))


if __name__ == "__main__":
    main()
