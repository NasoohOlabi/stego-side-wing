"""Standard API response envelope helpers."""
from __future__ import annotations

from typing import Any

from flask import Response, jsonify


def ok(data: Any = None, message: str | None = None, status: int = 200) -> tuple[Response, int]:
    payload: dict[str, Any] = {"ok": True}
    if message:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status


def fail(error: str, status: int = 400, details: Any = None) -> tuple[Response, int]:
    payload: dict[str, Any] = {"ok": False, "error": error}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status
