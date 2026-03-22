"""JSON Lines (JSONL) structured logging for the API process."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_RESERVED or key.startswith("_"):
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
    global _configured
    if _configured:
        return

    root_dir = repo_root or Path(__file__).resolve().parents[2]

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

    handlers: list[logging.Handler] = []
    if log_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        stderr_handler.setLevel(numeric)
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
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(formatter)
        fh.setLevel(numeric)
        handlers.append(fh)

    for h in handlers:
        root.addHandler(h)

    logging.captureWarnings(True)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _configured = True
