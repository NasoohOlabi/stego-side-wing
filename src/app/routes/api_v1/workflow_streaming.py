"""SSE + threaded workflow execution for API v1."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from contextvars import Token
from typing import Any

from flask import Response, request, stream_with_context

from app.routes.api_v1.constants import TRUE_VALUES
from infrastructure.json_logging import bind_trace_id, reset_trace_id
from services.workflow_run_tracker import (
    bind_run_id,
    end_run,
    register_run,
    reset_run_id,
    track_workflow,
)

log = logging.getLogger(__name__)


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in TRUE_VALUES
    return False


def wants_workflow_stream(body: dict[str, Any] | None = None) -> bool:
    # Workflow routes default to SSE; pass ?stream=0 or {"stream": false} to force JSON.
    query_flag = request.args.get("stream")
    if query_flag is not None:
        return is_truthy(query_flag)
    if isinstance(body, dict) and "stream" in body:
        return is_truthy(body.get("stream"))
    accept_header = (request.headers.get("Accept") or "").lower()
    if "text/event-stream" in accept_header:
        return True
    return True


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


_WORKFLOW_LOG_RESERVED_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
}


def workflow_log_payload(record: logging.LogRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "level": record.levelname.lower(),
        "logger": record.name,
        "message": record.getMessage(),
    }
    for key, value in record.__dict__.items():
        if key in _WORKFLOW_LOG_RESERVED_FIELDS or key.startswith("_"):
            continue
        payload[key] = value
    if record.exc_info:
        payload["exc_info"] = logging.Formatter().formatException(record.exc_info)
    return payload


class WorkflowLogHandler(logging.Handler):
    """Forwards workflow logger records into an SSE queue."""

    def __init__(self, events: queue.Queue[tuple[str, dict[str, Any]]]):
        super().__init__()
        self._events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = workflow_log_payload(record)
            self._events.put(("log", payload))
        except Exception:
            return


def sync_workflow(command: str, run_fn: Callable[[], Any]) -> Any:
    log.info(
        "workflow_sync_begin",
        extra={"event": "workflow", "mode": "sync", "command": command},
    )
    with track_workflow(command):
        return run_fn()


def stream_workflow(
    command: str,
    executor: Callable[[Callable[[str, dict[str, Any]], None]], Any],
    *,
    trace_id: str | None = None,
) -> Response:
    events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
    done = threading.Event()
    run_id = register_run(command, "stream")

    def emit(event: str, payload: dict[str, Any]) -> None:
        events.put((event, payload))

    def worker() -> None:
        trace_token: Token | None = None
        run_token: Token = bind_run_id(run_id)
        if trace_id:
            trace_token = bind_trace_id(trace_id)
        stream_started = time.perf_counter()
        log.info(
            "workflow_stream_begin",
            extra={
                "event": "workflow",
                "mode": "stream",
                "command": command,
                "run_id": run_id,
                "trace_id": trace_id,
            },
        )
        try:
            workflow_logger = logging.getLogger("workflows")
            original_level = workflow_logger.level
            level_changed = False
            log_handler = WorkflowLogHandler(events)
            log_handler.setFormatter(logging.Formatter("%(message)s"))
            log_handler.setLevel(logging.INFO)
            workflow_logger.addHandler(log_handler)
            if original_level > logging.INFO:
                workflow_logger.setLevel(logging.INFO)
                level_changed = True

            try:
                emit("status", {"phase": "started", "command": command, "run_id": run_id})
                result = executor(emit)
                elapsed_ms = int((time.perf_counter() - stream_started) * 1000)
                log.info(
                    "workflow_stream_complete",
                    extra={
                        "event": "workflow",
                        "mode": "stream",
                        "command": command,
                        "run_id": run_id,
                        "trace_id": trace_id,
                        "elapsed_ms": elapsed_ms,
                        "outcome": "ok",
                    },
                )
                emit("result", {"command": command, "result": result})
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - stream_started) * 1000)
                log.exception(
                    "workflow_stream_failed",
                    extra={
                        "event": "workflow",
                        "mode": "stream",
                        "command": command,
                        "run_id": run_id,
                        "trace_id": trace_id,
                        "elapsed_ms": elapsed_ms,
                        "outcome": "error",
                    },
                )
                emit(
                    "error",
                    {
                        "command": command,
                        "message": "Workflow execution failed",
                        "details": str(exc),
                    },
                )
            finally:
                workflow_logger.removeHandler(log_handler)
                if level_changed:
                    workflow_logger.setLevel(original_level)
        finally:
            if trace_token is not None:
                reset_trace_id(trace_token)
            reset_run_id(run_token)
            end_run(run_id)
            done.set()

    threading.Thread(target=worker, name=f"workflow-stream-{command}", daemon=True).start()

    def event_stream():
        yield sse("status", {"phase": "accepted", "command": command, "run_id": run_id})
        while True:
            try:
                event_name, payload = events.get(timeout=0.75)
                yield sse(event_name, payload)
            except queue.Empty:
                if done.is_set() and events.empty():
                    break
        yield sse("done", {"command": command})

    stream_iter = event_stream()
    response = Response(
        response=stream_with_context(stream_iter),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response
