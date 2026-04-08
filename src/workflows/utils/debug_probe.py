"""Tiny NDJSON debug logging for runtime triage."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from infrastructure.config import REPO_ROOT

DEBUG_LOG_PATH = REPO_ROOT / "debug-f0bcc9.log"
SESSION_ID = "f0bcc9"


def write_debug_probe(
    *,
    run_id: str | None,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    payload = {
        "sessionId": SESSION_ID,
        "runId": run_id or "",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
