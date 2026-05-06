"""Helpers for appending direct script process IDs to the repo pid log."""

from __future__ import annotations

import os
from pathlib import Path

from infrastructure.config import REPO_ROOT

PID_LOG_PATH = REPO_ROOT / "pid.log"


def append_current_pid_to_log() -> Path:
    """Append the current process id to the repo-root pid.log file."""
    PID_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PID_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()}\n")
    return PID_LOG_PATH
