"""API v1: health, state, filesystem, workflow LLM prompts, artifacts."""
from __future__ import annotations

import logging
from typing import Any

from flask import request
from pydantic import ValidationError

from app.routes.api_v1.blueprint import bp
from app.routes.api_v1.http_parsers import (
    json_body,
    query_bool,
    query_int,
)
from app.schemas.responses import fail, ok
from infrastructure.config import REPO_ROOT, STEPS
from infrastructure.json_logging import (
    TAG_API,
    TAG_HTTP,
    get_trace_id,
    structured_log_tag_catalog,
    structured_log_tag_ids,
)
from services.posts_service import get_post, list_posts, save_object, save_post
from services.state_service import (
    delete_path,
    get_paths_map,
    list_directory,
    read_json_file,
    write_json_file,
)
from workflows.utils.workflow_llm_prompts import WorkflowLlmPromptsDocument

logger = logging.getLogger(__name__)

@bp.route("/health", methods=["GET"])
def health() -> tuple[Any, int]:
    return ok(
        {
            "service": "stego-side-wing",
            "repo_root": str(REPO_ROOT),
            "step_count": len(STEPS),
        }
    )


@bp.route("/state/steps", methods=["GET"])
def state_steps() -> tuple[Any, int]:
    return ok({"steps": STEPS})


@bp.route("/state/paths", methods=["GET"])
def state_paths() -> tuple[Any, int]:
    return ok({"paths": get_paths_map()})


@bp.route("/logging/tags", methods=["GET"])
def logging_tags() -> tuple[Any, int]:
    """Structured JSONL log tag ids and descriptions (for filtering / UI)."""
    return ok(
        {
            "tags": structured_log_tag_catalog(),
            "tag_ids": structured_log_tag_ids(),
        }
    )


@bp.route("/state/logs", methods=["GET"])
def state_logs_info() -> tuple[Any, int]:
    """Current API JSONL log file path and size on disk (bytes)."""
    from app.routes import api_v1_routes as ar

    return ok(ar.get_api_log_file_stats())


@bp.route("/state/logs", methods=["DELETE"])
def state_logs_clear() -> tuple[Any, int]:
    """Truncate the API JSONL log file (same file as ``GET /state/logs``)."""
    from app.routes import api_v1_routes as ar

    stats = ar.get_api_log_file_stats()
    if not stats.get("file_logging_enabled"):
        return fail(
            "API file log is disabled (no file target; use default logging or enable file log)",
            status=400,
        )
    result = ar.clear_api_log_file()
    if not result.get("cleared"):
        return fail(
            result.get("reason", "Could not truncate log file"),
            status=500,
            details=result,
        )
    return ok(result, message="API log file truncated")


@bp.route("/state/fs/list", methods=["GET"])
def state_fs_list() -> tuple[Any, int]:
    rel_path = request.args.get("path", ".")
    recursive = query_bool("recursive", default=False)
    limit, err = query_int("limit", default=200)
    if err:
        return err
    assert limit is not None
    try:
        data = list_directory(relative_path=rel_path, recursive=recursive, limit=limit)
        return ok(data)
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/state/fs/read-json", methods=["GET"])
def state_fs_read_json() -> tuple[Any, int]:
    rel_path = request.args.get("path")
    if not rel_path:
        return fail("Missing required query parameter: path", status=400)
    try:
        return ok(read_json_file(rel_path))
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/state/fs/write-json", methods=["POST"])
def state_fs_write_json() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    rel_path = body.get("path")
    data = body.get("data")
    overwrite = bool(body.get("overwrite", True))
    if not isinstance(rel_path, str):
        return fail("'path' must be a string", status=400)
    if not isinstance(data, dict):
        return fail("'data' must be a JSON object", status=400)
    try:
        return ok(write_json_file(rel_path, data, overwrite=overwrite), status=201)
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/state/fs/delete", methods=["DELETE"])
def state_fs_delete() -> tuple[Any, int]:
    rel_path = request.args.get("path")
    if not rel_path:
        return fail("Missing required query parameter: path", status=400)
    recursive = query_bool("recursive", default=False)
    try:
        return ok(delete_path(rel_path, recursive=recursive))
    except ValueError as exc:
        return fail(str(exc), status=400)


def _workflow_llm_prompts_rel_path() -> str:
    """Repo-relative path for API responses, or absolute if path is outside ``REPO_ROOT``."""
    from app.routes import api_v1_routes as ar

    path = ar.workflow_llm_prompts_path().resolve()
    root = REPO_ROOT.resolve()
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        display = str(path).replace("\\", "/")
        logger.info(
            "prompts_path_outside_repo_root",
            extra={
                "event": "prompts_path",
                "tags": [TAG_API, TAG_HTTP],
                "component": "api_v1",
                "log_area": "workflow_llm_prompts",
                "path_display": "absolute_outside_repo",
                "resolved_path": display,
                "repo_root": str(root).replace("\\", "/"),
                "trace_id": get_trace_id(),
                "detail": "prompts file not under REPO_ROOT (e.g. monkeypathed tests); response uses absolute path",
            },
        )
        return display


def _log_prompts_route(action: str, **fields: Any) -> None:
    logger.info(
        "prompts_workflow_llm_route",
        extra={
            "event": "prompts_workflow_llm",
            "tags": [TAG_API, TAG_HTTP],
            "component": "api_v1",
            "log_area": "workflow_llm_prompts",
            "route_action": action,
            "trace_id": get_trace_id(),
            **fields,
        },
    )


@bp.route("/prompts/workflow-llm", methods=["GET"])
def prompts_workflow_llm_get() -> tuple[Any, int]:
    """Return workflow LLM prompt templates (reloads from disk before read)."""
    from app.routes import api_v1_routes as ar

    _log_prompts_route("get")
    ar.reload_prompts()
    doc = ar.get_prompts()
    _log_prompts_route("get_done", prompts_version=doc.version)
    return ok(
        {
            "prompts": doc.model_dump(mode="json"),
            "path": _workflow_llm_prompts_rel_path(),
        }
    )


@bp.route("/prompts/workflow-llm", methods=["PUT"])
def prompts_workflow_llm_put() -> tuple[Any, int]:
    """Replace workflow LLM prompts JSON (validated); reloads in-process cache."""
    from app.routes import api_v1_routes as ar

    _log_prompts_route("put")
    body, err = json_body()
    if err:
        return err
    assert body is not None
    raw = body.get("prompts")
    if not isinstance(raw, dict):
        return fail("'prompts' must be a JSON object", status=400)
    try:
        doc = WorkflowLlmPromptsDocument.model_validate(raw)
    except ValidationError as exc:
        logger.info(
            "prompts_workflow_llm_validation_failed",
            extra={
                "event": "prompts_workflow_llm",
                "tags": [TAG_API, TAG_HTTP],
                "component": "api_v1",
                "log_area": "workflow_llm_prompts",
                "route_action": "put",
                "outcome": "validation_error",
                "trace_id": get_trace_id(),
            },
        )
        return fail("Invalid prompts document", status=400, details=exc.errors())
    ar.save_workflow_llm_prompts_to_path(ar.workflow_llm_prompts_path(), doc)
    ar.reload_prompts()
    _log_prompts_route("put_done", prompts_version=doc.version)
    return ok(
        {
            "path": _workflow_llm_prompts_rel_path(),
            "written": True,
        },
        message="Workflow LLM prompts updated",
        status=201,
    )


@bp.route("/prompts/workflow-llm/reset", methods=["POST"])
def prompts_workflow_llm_reset() -> tuple[Any, int]:
    """Restore baked-in default prompts and reload cache."""
    from app.routes import api_v1_routes as ar

    _log_prompts_route("reset")
    ar.save_workflow_llm_prompts_to_path(
        ar.workflow_llm_prompts_path(),
        ar.default_workflow_llm_prompts(),
    )
    ar.reload_prompts()
    _log_prompts_route("reset_done", prompts_version=ar.get_prompts().version)
    return ok(
        {
            "path": _workflow_llm_prompts_rel_path(),
            "reset": True,
        },
        message="Workflow LLM prompts reset to defaults",
    )


@bp.route("/artifacts/posts", methods=["GET"])
def artifacts_posts() -> tuple[Any, int]:
    step = request.args.get("step")
    if not step:
        return fail("Missing required query parameter: step", status=400)
    if step not in STEPS:
        return fail(f"Invalid step: {step}", status=400)

    count, err = query_int("count", default=50)
    if err:
        return err
    offset, err = query_int("offset", default=0)
    if err:
        return err
    assert count is not None
    assert offset is not None
    tag = request.args.get("tag")
    try:
        return ok(list_posts(count=count, step=step, tag=tag, offset=offset))
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/artifacts/post", methods=["GET"])
def artifacts_get_post() -> tuple[Any, int]:
    step = request.args.get("step")
    post = request.args.get("post")
    if not step or not post:
        return fail("Missing required query parameters: step, post", status=400)
    if step not in STEPS:
        return fail(f"Invalid step: {step}", status=400)
    try:
        return ok(get_post(post=post, step=step))
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/artifacts/post", methods=["POST"])
def artifacts_save_post() -> tuple[Any, int]:
    step = request.args.get("step")
    if not step:
        return fail("Missing required query parameter: step", status=400)
    if step not in STEPS:
        return fail(f"Invalid step: {step}", status=400)
    body, err = json_body()
    if err:
        return err
    assert body is not None
    try:
        return ok(save_post(post_data=body, step=step), status=201)
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/artifacts/object", methods=["POST"])
def artifacts_save_object() -> tuple[Any, int]:
    step = request.args.get("step")
    filename = request.args.get("filename")
    if not step or not filename:
        return fail("Missing required query parameters: step, filename", status=400)
    if step not in STEPS:
        return fail(f"Invalid step: {step}", status=400)
    body, err = json_body()
    if err:
        return err
    assert body is not None
    try:
        return ok(save_object(data=body, step=step, filename=filename), status=201)
    except ValueError as exc:
        return fail(str(exc), status=400)
