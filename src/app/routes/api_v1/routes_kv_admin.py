"""API v1: KV store and admin endpoints."""
from __future__ import annotations

import logging
from typing import Any

from flask import current_app, request

from app.routes.api_v1.blueprint import bp
from app.routes.api_v1.http_parsers import json_body, query_int
from app.schemas.responses import fail, ok
from services.kv_service import (
    delete_value,
    get_value,
    init_db,
    list_values,
    migrate_json_to_sqlite,
    set_value,
)
from services.state_service import clear_cache, get_cache_stats

logger = logging.getLogger(__name__)

@bp.route("/kv", methods=["GET"])
def kv_list() -> tuple[Any, int]:
    limit, err = query_int("limit", default=100)
    if err:
        return err
    offset, err = query_int("offset", default=0)
    if err:
        return err
    assert limit is not None
    assert offset is not None
    try:
        return ok(list_values(limit=limit, offset=offset))
    except Exception as exc:
        return fail("KV list failed", status=500, details=str(exc))


@bp.route("/kv/<key>", methods=["GET"])
def kv_get(key: str) -> tuple[Any, int]:
    try:
        result = get_value(key)
        if result is None:
            return fail(f'Key "{key}" not found', status=404)
        return ok(result)
    except Exception as exc:
        return fail("KV get failed", status=500, details=str(exc))


@bp.route("/kv/<key>", methods=["PUT"])
def kv_put(key: str) -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    if "value" not in body:
        return fail('Missing "value" in request body', status=400)
    try:
        return ok(set_value(key, body["value"]), status=201)
    except Exception as exc:
        return fail("KV set failed", status=500, details=str(exc))


@bp.route("/kv/<key>", methods=["DELETE"])
def kv_delete(key: str) -> tuple[Any, int]:
    try:
        result = delete_value(key)
        return ok(result)
    except Exception as exc:
        return fail("KV delete failed", status=500, details=str(exc))


@bp.route("/admin/cache/stats", methods=["GET"])
def admin_cache_stats() -> tuple[Any, int]:
    try:
        return ok({"caches": get_cache_stats()})
    except Exception as exc:
        return fail("Could not read cache stats", status=500, details=str(exc))


@bp.route("/admin/cache/clear", methods=["POST"])
def admin_cache_clear() -> tuple[Any, int]:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    target = str(body.get("target", "all"))
    try:
        data = clear_cache(target)
        cache = current_app.config.get("cache")
        if target in {"all", "flask"} and cache is not None:
            cache.clear()
        return ok(data)
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        return fail("Cache clear failed", status=500, details=str(exc))


@bp.route("/admin/kv/migrate", methods=["POST"])
def admin_kv_migrate() -> tuple[Any, int]:
    try:
        migrate_json_to_sqlite()
        init_db()
        return ok({"migrated": True})
    except Exception as exc:
        return fail("KV migration failed", status=500, details=str(exc))
