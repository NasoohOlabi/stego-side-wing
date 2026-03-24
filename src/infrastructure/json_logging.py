"""JSON Lines (JSONL) structured logging for the API process."""
from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

# Canonical tag list (descriptions + ids). Exposed via GET /api/v1/logging/tags.
STRUCTURED_LOG_TAG_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "api",
        "description": (
            "Default tag on every API JSON log line (StructuredContextFilter)."
        ),
    },
    {
        "id": "trace",
        "description": (
            "Added when trace_id is bound (e.g. during an HTTP request)."
        ),
    },
    {
        "id": "function",
        "description": (
            "Function boundary logs; often with event function.start "
            "(see log_function_start)."
        ),
    },
    {
        "id": "process",
        "description": (
            "Process or subsystem boundaries; often with event process.start "
            "(see log_process_start)."
        ),
    },
    {
        "id": "lifecycle",
        "description": (
            "Cross-cutting lifecycle signals; combined with function, process, or http."
        ),
    },
    {
        "id": "http",
        "description": "HTTP access and request-scoped application logs.",
    },
    {
        "id": "workflow",
        "description": "Workflow runner and pipeline-related log lines.",
    },
)

_LOG_TAG_IDS_KNOWN = frozenset(row["id"] for row in STRUCTURED_LOG_TAG_CATALOG)


def structured_log_tag_catalog() -> list[dict[str, str]]:
    """Copy of tag definitions for JSON APIs and clients."""
    return [{"id": e["id"], "description": e["description"]} for e in STRUCTURED_LOG_TAG_CATALOG]


def structured_log_tag_ids() -> list[str]:
    """Ordered tag ids (same order as STRUCTURED_LOG_TAG_CATALOG)."""
    return [e["id"] for e in STRUCTURED_LOG_TAG_CATALOG]


def _require_log_tag(tag_id: str) -> str:
    if tag_id not in _LOG_TAG_IDS_KNOWN:
        raise RuntimeError(f"unknown structured log tag {tag_id!r}; fix STRUCTURED_LOG_TAG_CATALOG")
    return tag_id


TAG_API = _require_log_tag("api")
TAG_TRACE = _require_log_tag("trace")
TAG_FUNCTION = _require_log_tag("function")
TAG_PROCESS = _require_log_tag("process")
TAG_LIFECYCLE = _require_log_tag("lifecycle")
TAG_HTTP = _require_log_tag("http")
TAG_WORKFLOW = _require_log_tag("workflow")

_trace_id_ctx: ContextVar[str | None] = ContextVar("structured_trace_id", default=None)

# LogRecord attributes that are not treated as user "extra" fields.
_LOGRECORD_RESERVED: frozenset[str] = frozenset(
    {
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
        "taskName",
    }
)

_configured = False
# Set when a FileHandler is attached for the API JSONL log (see configure_api_logging).
_resolved_log_path: Path | None = None


def get_trace_id() -> str | None:
    return _trace_id_ctx.get()


def bind_trace_id(trace_id: str) -> Token:
    """Bind correlation id for the current context; reset with reset_trace_id."""
    return _trace_id_ctx.set(trace_id)


def reset_trace_id(token: Token) -> None:
    _trace_id_ctx.reset(token)


def _normalize_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        return [tags] if tags else []
    if isinstance(tags, (list, tuple, set)):
        return [str(t) for t in tags if t is not None and str(t)]
    return [str(tags)]


def log_function_start(
    logger: logging.Logger, qualname: str, *, level: int = logging.DEBUG, **fields: Any
) -> None:
    """Tag: function; event: function.start — filter with .tags|index(\"function\")."""
    logger.log(
        level,
        "function_start",
        extra={
            "event": "function.start",
            "tags": [TAG_FUNCTION, TAG_LIFECYCLE],
            "function": qualname,
            **fields,
        },
    )


def log_process_start(
    logger: logging.Logger, process_name: str, *, level: int = logging.INFO, **fields: Any
) -> None:
    """Tag: process; event: process.start — avoids LogRecord's reserved \"process\" field."""
    logger.log(
        level,
        "process_start",
        extra={
            "event": "process.start",
            "tags": [TAG_PROCESS, TAG_LIFECYCLE],
            "process_name": process_name,
            **fields,
        },
    )


class StructuredContextFilter(logging.Filter):
    """Attach trace_id, process_pid, and merged tags to every record."""

    def __init__(self, default_tags: Sequence[str] | None = None):
        super().__init__()
        self._default_tags: list[str] = list(default_tags) if default_tags else [TAG_API]

    def filter(self, record: logging.LogRecord) -> bool:
        tid = get_trace_id()
        record.trace_id = tid if tid is not None else getattr(record, "trace_id", None)

        tags: list[str] = list(self._default_tags)
        for t in _normalize_tags(getattr(record, "tags", None)):
            if t not in tags:
                tags.append(t)
        if tid is not None and TAG_TRACE not in tags:
            tags.append(TAG_TRACE)
        record.tags = tags

        if not hasattr(record, "process_pid"):
            record.process_pid = os.getpid()

        return True


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


_JSON_TOP_KEYS = frozenset(
    {"trace_id", "tags", "event", "process_pid"}
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line (parseable JSONL)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        tid = getattr(record, "trace_id", None)
        if tid is not None:
            payload["trace_id"] = tid
        tags = getattr(record, "tags", None)
        payload["tags"] = _json_safe(tags if tags is not None else [TAG_API])
        event = getattr(record, "event", None)
        if event:
            payload["event"] = _json_safe(event)
        payload["process_pid"] = _json_safe(
            getattr(record, "process_pid", os.getpid())
        )
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_RESERVED or key.startswith("_"):
                continue
            if key in _JSON_TOP_KEYS:
                continue
            payload[key] = _json_safe(value)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_api_logging(
    *,
    level: str = "INFO",
    log_file: str | Path | None = None,
    log_stderr: bool = True,
    enable_file_log: bool = True,
    repo_root: Path | None = None,
) -> None:
    """
    Attach JSONL handlers to the root logger (idempotent).

    Environment (used when arguments are None):
      API_LOG_LEVEL — default INFO
      API_LOG_FILE — path relative to repo root or absolute; empty string disables file logging
    """
    global _configured, _resolved_log_path
    if _configured:
        return

    root_dir = repo_root or Path(__file__).resolve().parents[2]
    _resolved_log_path = None

    env_file = os.environ.get("API_LOG_FILE")
    resolved = (level or "").strip()
    if not resolved:
        resolved = (os.environ.get("API_LOG_LEVEL") or "").strip() or "INFO"
    resolved_level = resolved.upper()
    numeric = getattr(logging, resolved_level, logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(numeric)

    formatter = JsonFormatter()
    context_filter = StructuredContextFilter()

    handlers: list[logging.Handler] = []
    if log_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        stderr_handler.setLevel(numeric)
        stderr_handler.addFilter(context_filter)
        handlers.append(stderr_handler)

    file_path: Path | None = None
    if not enable_file_log:
        file_path = None
    elif log_file is not None:
        raw = str(log_file).strip()
        file_path = Path(raw) if raw else None
    elif env_file is not None:
        raw = env_file.strip()
        file_path = Path(raw) if raw else None
    else:
        file_path = root_dir / "logs" / "api.jsonl"

    if file_path is not None:
        if not file_path.is_absolute():
            file_path = root_dir / file_path
        file_path = file_path.resolve()
        _resolved_log_path = file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(formatter)
        fh.setLevel(numeric)
        fh.addFilter(context_filter)
        handlers.append(fh)

    for h in handlers:
        root.addHandler(h)

    logging.captureWarnings(True)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _configured = True


def get_api_log_file_stats() -> dict[str, Any]:
    """
    Size and path of the configured API JSONL log file.

    When file logging is disabled, ``file_logging_enabled`` is False and ``path`` is None.
    """
    if _resolved_log_path is None:
        return {
            "file_logging_enabled": False,
            "path": None,
            "bytes": 0,
        }
    p = _resolved_log_path
    if not p.exists():
        return {
            "file_logging_enabled": True,
            "path": str(p),
            "bytes": 0,
        }
    return {
        "file_logging_enabled": True,
        "path": str(p),
        "bytes": int(p.stat().st_size),
    }


def clear_api_log_file() -> dict[str, Any]:
    """
    Truncate the API JSONL log to zero bytes.

    Uses the active ``logging.FileHandler`` stream when present so the file stays
    consistent with the open handle (important on Windows).
    """
    if _resolved_log_path is None:
        return {"cleared": False, "path": None, "reason": "file_logging_disabled"}

    target = _resolved_log_path.resolve()
    cleared = False
    root = logging.getLogger()
    for h in root.handlers:
        if not isinstance(h, logging.FileHandler):
            continue
        try:
            base = Path(h.baseFilename).resolve()
        except Exception:
            continue
        if base != target:
            continue
        h.acquire()
        try:
            stream = getattr(h, "stream", None)
            if stream is not None:
                stream.seek(0)
                stream.truncate(0)
                h.flush()
            cleared = True
        finally:
            h.release()
        break

    if not cleared and target.exists():
        try:
            target.write_text("", encoding="utf-8")
            cleared = True
        except OSError:
            return {"cleared": False, "path": str(target), "reason": "truncate_failed"}
    elif not cleared and not target.exists():
        return {"cleared": True, "path": str(target), "reason": "file_absent"}

    return {"cleared": cleared, "path": str(target)}
