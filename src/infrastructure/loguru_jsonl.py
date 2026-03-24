"""Loguru sinks emitting JSONL aligned with observability rules (timestamp, level, component, trace_id, message)."""
from __future__ import annotations

import inspect
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO, cast

from loguru import logger

_loguru_file_stream: TextIO | None = None
_loguru_file_path: Path | None = None
_loguru_configured = False

_LOGRECORD_STD_KEYS = frozenset(
    logging.LogRecord(
        name="",
        level=logging.NOTSET,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__.keys()
)


def _iso_utc_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _payload_from_loguru_message(message: Any) -> dict[str, Any]:
    from infrastructure.json_logging import get_trace_id

    rec = message.record
    extra = dict(rec["extra"])
    tid = extra.pop("trace_id", None)
    if tid is None:
        tid = get_trace_id()
    comp = extra.pop("component", None) or "root"
    payload: dict[str, Any] = {
        "timestamp": _iso_utc_z(rec["time"]),
        "level": rec["level"].name,
        "component": comp,
        "trace_id": tid,
        "message": rec["message"],
    }
    exc = rec["exception"]
    if exc is not None:
        try:
            typ, val, steb = exc  # type: ignore[misc]
            payload["exc_info"] = "".join(
                traceback.format_exception(typ, val, steb)
            ).rstrip()
        except Exception:
            payload["exc_info"] = str(exc)
    for key, value in extra.items():
        if key not in payload:
            payload[key] = _json_safe(value)
    return payload


def _write_jsonl_line(stream: TextIO, message: Any) -> None:
    line = json.dumps(_payload_from_loguru_message(message), ensure_ascii=False, default=str)
    stream.write(line + "\n")
    stream.flush()


def _patcher(record: Any) -> None:
    from infrastructure.json_logging import get_trace_id

    rec = cast(dict[str, Any], record)
    extra = rec["extra"]
    if "trace_id" not in extra or extra.get("trace_id") is None:
        ctx = get_trace_id()
        if ctx is not None:
            extra["trace_id"] = ctx
    extra.setdefault("component", "root")


class InterceptHandler(logging.Handler):
    """Forward stdlib logging records into Loguru (JSONL sinks)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = str(record.levelno)

        depth = 6
        frame = inspect.currentframe()
        for _ in range(depth):
            if frame is None:
                break
            frame = frame.f_back
        log_file = getattr(logging, "__file__", "")
        while frame is not None and log_file and frame.f_code.co_filename == log_file:
            frame = frame.f_back
            depth += 1

        bind_kv: dict[str, Any] = {"component": getattr(record, "component", None) or record.name}
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_STD_KEYS or key.startswith("_"):
                continue
            if key == "component":
                continue
            bind_kv[key] = value

        try:
            log = logger.bind(**bind_kv)
            log.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
        except Exception:
            self.handleError(record)


def _loguru_min_level(level_name: str) -> str:
    n = level_name.upper()
    if n == "NOTSET":
        return "TRACE"
    return n


def configure_loguru_jsonl(
    *,
    level: str,
    log_stderr: bool,
    file_path: Path | None,
) -> None:
    """Idempotent: single process-wide Loguru JSONL configuration."""
    global _loguru_configured, _loguru_file_stream, _loguru_file_path
    if _loguru_configured:
        return

    logger.remove()
    logger.patch(_patcher)

    resolved = level.upper()
    numeric = getattr(logging, resolved, logging.INFO)
    min_level = _loguru_min_level(resolved)

    if log_stderr:

        def _stderr_sink(message: Any) -> None:
            _write_jsonl_line(sys.stderr, message)

        logger.add(_stderr_sink, level=min_level)

    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        _loguru_file_path = file_path.resolve()
        _loguru_file_stream = open(_loguru_file_path, "a", encoding="utf-8")

        def _file_sink(message: Any) -> None:
            if _loguru_file_stream is None:
                return
            _write_jsonl_line(_loguru_file_stream, message)

        logger.add(_file_sink, level=min_level)

    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(numeric)
    _loguru_configured = True


def loguru_resolved_file_path() -> Path | None:
    return _loguru_file_path


def close_loguru_file_stream() -> None:
    global _loguru_file_stream
    if _loguru_file_stream is not None:
        try:
            _loguru_file_stream.flush()
            _loguru_file_stream.close()
        except OSError:
            pass
        _loguru_file_stream = None


def truncate_loguru_file() -> bool:
    """Truncate the active Loguru file stream if open."""
    if _loguru_file_stream is None or _loguru_file_path is None:
        return False
    try:
        _loguru_file_stream.seek(0)
        _loguru_file_stream.truncate(0)
        _loguru_file_stream.flush()
        return True
    except OSError:
        return False
