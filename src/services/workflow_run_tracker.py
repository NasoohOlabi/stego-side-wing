"""In-process registry of workflow runs (API process only)."""
from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator, Iterator

_lock = threading.RLock()
_runs: dict[str, "_RunRecord"] = {}


@dataclass
class _RunRecord:
    run_id: str
    command: str
    mode: str
    started_at: float


def register_run(command: str, mode: str) -> str:
    run_id = uuid.uuid4().hex
    with _lock:
        _runs[run_id] = _RunRecord(
            run_id=run_id,
            command=command,
            mode=mode,
            started_at=time.time(),
        )
    return run_id


def end_run(run_id: str) -> None:
    with _lock:
        _runs.pop(run_id, None)


def iter_snapshot() -> Iterator[dict[str, Any]]:
    now = time.time()
    with _lock:
        records = list(_runs.values())
    for r in records:
        yield {
            "id": r.run_id,
            "command": r.command,
            "mode": r.mode,
            "started_at": r.started_at,
            "elapsed_ms": int((now - r.started_at) * 1000),
        }


@contextmanager
def track_workflow(command: str) -> Generator[str, None, None]:
    run_id = register_run(command, "sync")
    try:
        yield run_id
    finally:
        end_run(run_id)
