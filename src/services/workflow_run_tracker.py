"""In-process registry of workflow runs (API process only)."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from contextvars import ContextVar, Token
from typing import Any, Generator, Iterator

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_runs: dict[str, "_RunRecord"] = {}
_run_id_ctx: ContextVar[str | None] = ContextVar("workflow_run_id", default=None)


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
    logger.info(
        "workflow_run_register",
        extra={
            "event": "workflow_run",
            "action": "register",
            "run_id": run_id,
            "command": command,
            "mode": mode,
        },
    )
    return run_id


def get_run_id() -> str | None:
    return _run_id_ctx.get()


def bind_run_id(run_id: str) -> Token:
    return _run_id_ctx.set(run_id)


def reset_run_id(token: Token) -> None:
    _run_id_ctx.reset(token)


def end_run(run_id: str) -> None:
    with _lock:
        existed = run_id in _runs
        _runs.pop(run_id, None)
    logger.info(
        "workflow_run_end",
        extra={
            "event": "workflow_run",
            "action": "end",
            "run_id": run_id,
            "had_record": existed,
        },
    )


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
    token = bind_run_id(run_id)
    try:
        yield run_id
    finally:
        reset_run_id(token)
        end_run(run_id)
