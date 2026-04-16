"""JSON/query parsing helpers for API v1 (pure request → typed values + errors)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import request

from app.routes.api_v1.constants import TRUE_VALUES
from app.schemas.responses import fail
from infrastructure.config import METRICS_DIR, POSTS_DIRECTORY
from services.state_service import safe_repo_path
from services.workflow_facade import stable_hash, text_preview


def json_body() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, fail("Invalid or missing JSON body", status=400)
    return body, None


def query_int(name: str, default: int | None = None) -> tuple[int | None, tuple[Any, int] | None]:
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        return int(raw), None
    except ValueError:
        return None, fail(f"Query parameter '{name}' must be an integer", status=400)


def query_bool(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def body_int(body: dict[str, Any], key: str, default: int) -> tuple[int | None, tuple[Any, int] | None]:
    value = body.get(key, default)
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, fail(f"'{key}' must be an integer", status=400)


def body_bool(
    body: dict[str, Any], key: str, default: bool = False
) -> tuple[bool, tuple[Any, int] | None]:
    value = body.get(key, default)
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)):
        return value != 0, None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_VALUES:
            return True, None
        if normalized in {"0", "false", "no", "off", ""}:
            return False, None
    return False, fail(f"'{key}' must be a boolean", status=400)


def optional_body_str(body: dict[str, Any], key: str) -> tuple[str | None, tuple[Any, int] | None]:
    value = body.get(key)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, fail(f"'{key}' must be a string when provided", status=400)
    normalized = value.strip()
    return normalized or None, None


def body_metrics_output_dir(body: dict[str, Any]) -> tuple[Path | None, tuple[Any, int] | None]:
    raw = body.get("output_dir", "output-results")
    if not isinstance(raw, str):
        return None, fail("'output_dir' must be a string", status=400)
    rel = raw.strip() or "output-results"
    try:
        return safe_repo_path(rel), None
    except ValueError as exc:
        return None, fail(str(exc), status=400)


def body_metrics_dir(body: dict[str, Any]) -> tuple[Path | None, tuple[Any, int] | None]:
    raw = body.get("metrics_dir")
    if raw is None:
        return METRICS_DIR, None
    if not isinstance(raw, str):
        return None, fail("'metrics_dir' must be a string", status=400)
    stripped = raw.strip()
    if not stripped:
        return METRICS_DIR, None
    try:
        return safe_repo_path(stripped), None
    except ValueError as exc:
        return None, fail(str(exc), status=400)


def body_metrics_dataset_dir(body: dict[str, Any]) -> tuple[Path | None, tuple[Any, int] | None]:
    raw = body.get("dataset_dir", POSTS_DIRECTORY)
    if not isinstance(raw, str):
        return None, fail("'dataset_dir' must be a string", status=400)
    rel = raw.strip() or POSTS_DIRECTORY
    try:
        return safe_repo_path(rel), None
    except ValueError as exc:
        return None, fail(str(exc), status=400)


def body_metrics_output_basename(body: dict[str, Any]) -> tuple[str | None, tuple[Any, int] | None]:
    raw = body.get("filename")
    if not isinstance(raw, str) or not raw.strip():
        return None, fail("'filename' must be a non-empty string", status=400)
    stripped = raw.strip()
    if "/" in stripped or "\\" in stripped:
        return None, fail("'filename' must be a basename only (no path separators)", status=400)
    name = Path(stripped).name
    if name != stripped:
        return None, fail("'filename' must be a basename only", status=400)
    if name in (".", "..") or name.startswith(".."):
        return None, fail("invalid filename", status=400)
    if not name.lower().endswith(".json"):
        return None, fail("'filename' must end with .json", status=400)
    return name, None


def query_metrics_dir_param() -> tuple[Path | None, tuple[Any, int] | None]:
    raw = request.args.get("metrics_dir")
    if raw is None or not str(raw).strip():
        return METRICS_DIR, None
    try:
        return safe_repo_path(str(raw).strip()), None
    except ValueError as exc:
        return None, fail(str(exc), status=400)


def required_body_str(body: dict[str, Any], key: str) -> tuple[str | None, tuple[Any, int] | None]:
    value, err = optional_body_str(body, key)
    if err:
        return None, err
    if not value:
        return None, fail(f"'{key}' must be a non-empty string", status=400)
    return value, None


def summarize_preview_post(post: dict[str, Any]) -> dict[str, Any]:
    selftext = post.get("selftext")
    search_results = post.get("search_results")
    angles = post.get("angles")
    return {
        "id": post.get("id"),
        "keys": sorted(post.keys()),
        "hash": stable_hash(post),
        "selftext_length": len(selftext) if isinstance(selftext, str) else 0,
        "selftext_preview": text_preview(selftext) if isinstance(selftext, str) else "",
        "search_results_count": len(search_results) if isinstance(search_results, list) else 0,
        "angles_count": len(angles) if isinstance(angles, list) else 0,
        "options_count": post.get("options_count"),
    }


def preview_response(
    preview: dict[str, Any],
    include_post: bool,
) -> dict[str, Any]:
    post = preview.get("post")
    if not isinstance(post, dict):
        return preview
    payload = {"report": preview.get("report")}
    if include_post:
        payload["post"] = post
    else:
        payload["post_summary"] = summarize_preview_post(post)
    return payload


def optional_payload_field(
    body: dict[str, Any], key: str = "payload"
) -> tuple[str | None, tuple[Any, int] | None]:
    """Optional stego-style payload: string, or JSON object/array coerced to a string."""
    value = body.get(key)
    if value is None:
        return None, None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None, None
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, separators=(",", ":"), default=str), None
        except (TypeError, ValueError):
            return None, fail(
                f"'{key}' must be JSON-serializable when provided as object or array", status=400
            )
    if isinstance(value, (bool, int, float)):
        return str(value), None
    return None, fail(
        f"'{key}' must be a string, number, boolean, object, or array when provided",
        status=400,
    )
