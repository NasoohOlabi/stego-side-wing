"""Versioned API routes for workflows, artifacts, tools, state, and admin."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Optional

from flask import Blueprint, Response, current_app, request, stream_with_context

from app.schemas.responses import fail, ok
from infrastructure.config import REPO_ROOT, STEPS
from services.analysis_service import fetch_url_content, fetch_url_content_crawl4ai, process_post_file
from services.angles_service import analyze_angles
from services.kv_service import delete_value, get_value, init_db, list_values, migrate_json_to_sqlite, set_value
from services.posts_service import get_post, list_posts, save_object, save_post
from services.search_service import search_bing, search_google, search_news_api, search_ollama
from services.semantic_service import find_best_match, semantic_search
from services.state_service import (
    clear_cache,
    delete_path,
    get_cache_stats,
    get_paths_map,
    list_directory,
    read_json_file,
    write_json_file,
)
from services.workflow_run_tracker import end_run, iter_snapshot, register_run, track_workflow
from workflows.runner import WorkflowRunner

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
runner = WorkflowRunner()
WORKFLOW_COMMANDS = (
    "data-load",
    "research",
    "gen-angles",
    "stego",
    "decode",
    "gen-terms",
    "full",
)
TRUE_VALUES = {"1", "true", "yes", "on"}


def _json_body() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, fail("Invalid or missing JSON body", status=400)
    return body, None


def _query_int(name: str, default: Optional[int] = None) -> tuple[Optional[int], tuple[Any, int] | None]:
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        return int(raw), None
    except ValueError:
        return None, fail(f"Query parameter '{name}' must be an integer", status=400)


def _query_bool(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _body_int(body: dict[str, Any], key: str, default: int) -> tuple[int | None, tuple[Any, int] | None]:
    value = body.get(key, default)
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, fail(f"'{key}' must be an integer", status=400)


def _body_bool(
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


def _optional_body_str(body: dict[str, Any], key: str) -> tuple[str | None, tuple[Any, int] | None]:
    value = body.get(key)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, fail(f"'{key}' must be a string when provided", status=400)
    normalized = value.strip()
    return normalized or None, None


def _optional_payload_field(body: dict[str, Any], key: str = "payload") -> tuple[str | None, tuple[Any, int] | None]:
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
            return None, fail(f"'{key}' must be JSON-serializable when provided as object or array", status=400)
    if isinstance(value, (bool, int, float)):
        return str(value), None
    return None, fail(
        f"'{key}' must be a string, number, boolean, object, or array when provided",
        status=400,
    )


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in TRUE_VALUES
    return False


def _wants_workflow_stream(body: Optional[dict[str, Any]] = None) -> bool:
    # Workflow routes default to SSE; pass ?stream=0 or {"stream": false} to force JSON.
    query_flag = request.args.get("stream")
    if query_flag is not None:
        return _is_truthy(query_flag)
    if isinstance(body, dict) and "stream" in body:
        return _is_truthy(body.get("stream"))
    accept_header = (request.headers.get("Accept") or "").lower()
    if "text/event-stream" in accept_header:
        return True
    return True


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


class _WorkflowLogHandler(logging.Handler):
    def __init__(self, events: "queue.Queue[tuple[str, dict[str, Any]]]"):
        super().__init__()
        self._events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if not message:
                return
            self._events.put(
                (
                    "log",
                    {
                        "level": record.levelname.lower(),
                        "logger": record.name,
                        "message": message,
                    },
                )
            )
        except Exception:
            return


def _sync_workflow(command: str, run_fn: Callable[[], Any]) -> Any:
    with track_workflow(command):
        return run_fn()


def _stream_workflow(
    command: str,
    executor: Callable[[Callable[[str, dict[str, Any]], None]], Any],
) -> Response:
    events: "queue.Queue[tuple[str, dict[str, Any]]]" = queue.Queue()
    done = threading.Event()

    def _emit(event: str, payload: dict[str, Any]) -> None:
        events.put((event, payload))

    def _worker() -> None:
        run_id = register_run(command, "stream")
        try:
            workflow_logger = logging.getLogger("workflows")
            original_level = workflow_logger.level
            level_changed = False
            log_handler = _WorkflowLogHandler(events)
            log_handler.setFormatter(logging.Formatter("%(message)s"))
            log_handler.setLevel(logging.INFO)
            workflow_logger.addHandler(log_handler)
            if original_level > logging.INFO:
                workflow_logger.setLevel(logging.INFO)
                level_changed = True

            try:
                _emit("status", {"phase": "started", "command": command})
                result = executor(_emit)
                _emit("result", {"command": command, "result": result})
            except Exception as exc:
                _emit(
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
            end_run(run_id)
            done.set()

    worker = threading.Thread(target=_worker, name=f"workflow-stream-{command}", daemon=True)
    worker.start()

    @stream_with_context
    def _event_stream():
        started_at = time.time()
        yield _sse("status", {"phase": "accepted", "command": command})
        while True:
            try:
                event_name, payload = events.get(timeout=0.75)
                yield _sse(event_name, payload)
            except queue.Empty:
                if done.is_set() and events.empty():
                    break
                elapsed_ms = int((time.time() - started_at) * 1000)
                yield _sse("heartbeat", {"command": command, "elapsed_ms": elapsed_ms})
        yield _sse("done", {"command": command})

    response = Response(_event_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response


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


@bp.route("/state/fs/list", methods=["GET"])
def state_fs_list() -> tuple[Any, int]:
    rel_path = request.args.get("path", ".")
    recursive = _query_bool("recursive", default=False)
    limit, err = _query_int("limit", default=200)
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
    body, err = _json_body()
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
    recursive = _query_bool("recursive", default=False)
    try:
        return ok(delete_path(rel_path, recursive=recursive))
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/artifacts/posts", methods=["GET"])
def artifacts_posts() -> tuple[Any, int]:
    step = request.args.get("step")
    if not step:
        return fail("Missing required query parameter: step", status=400)
    if step not in STEPS:
        return fail(f"Invalid step: {step}", status=400)

    count, err = _query_int("count", default=50)
    if err:
        return err
    offset, err = _query_int("offset", default=0)
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
    body, err = _json_body()
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
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    try:
        return ok(save_object(data=body, step=step, filename=filename), status=201)
    except ValueError as exc:
        return fail(str(exc), status=400)


@bp.route("/workflows/data-load", methods=["POST"])
def wf_data_load() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    count = body.get("count", 100)
    offset = body.get("offset", 0)
    batch_size = body.get("batch_size", 5)
    try:
        parsed_count = int(count)
        parsed_offset = int(offset)
        parsed_batch_size = int(batch_size)
    except (TypeError, ValueError):
        return fail("'count', 'offset', and 'batch_size' must be integers", status=400)

    if _wants_workflow_stream(body):
        return _stream_workflow(
            "data-load",
            lambda emit: runner.run_data_load(
                count=parsed_count,
                offset=parsed_offset,
                batch_size=parsed_batch_size,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
        )

    try:
        data = _sync_workflow(
            "data-load",
            lambda: runner.run_data_load(
                count=parsed_count,
                offset=parsed_offset,
                batch_size=parsed_batch_size,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/research", methods=["POST"])
def wf_research() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    count = body.get("count", 1)
    offset = body.get("offset", 0)
    try:
        parsed_count = int(count)
        parsed_offset = int(offset)
    except (TypeError, ValueError):
        return fail("'count' and 'offset' must be integers", status=400)

    if _wants_workflow_stream(body):
        return _stream_workflow(
            "research",
            lambda emit: runner.run_research(
                count=parsed_count,
                offset=parsed_offset,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
        )

    try:
        data = _sync_workflow(
            "research",
            lambda: runner.run_research(
                count=parsed_count,
                offset=parsed_offset,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/gen-angles", methods=["POST"])
def wf_gen_angles() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    count = body.get("count", 1)
    offset = body.get("offset", 0)
    try:
        parsed_count = int(count)
        parsed_offset = int(offset)
    except (TypeError, ValueError):
        return fail("'count' and 'offset' must be integers", status=400)

    if _wants_workflow_stream(body):
        return _stream_workflow(
            "gen-angles",
            lambda emit: runner.run_gen_angles(
                count=parsed_count,
                offset=parsed_offset,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
        )

    try:
        data = _sync_workflow(
            "gen-angles",
            lambda: runner.run_gen_angles(
                count=parsed_count,
                offset=parsed_offset,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/stego", methods=["POST"])
def wf_stego() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    post_id, err = _optional_body_str(body, "post_id")
    if err:
        return err
    payload, err = _optional_payload_field(body, "payload")
    if err:
        return err
    tag, err = _optional_body_str(body, "tag")
    if err:
        return err
    list_offset = body.get("list_offset", 1)
    try:
        parsed_list_offset = int(list_offset)
    except (TypeError, ValueError):
        return fail("'list_offset' must be an integer", status=400)
    run_all, err = _body_bool(body, "run_all", default=False)
    if err:
        return err
    max_posts = body.get("max_posts")
    parsed_max_posts: Optional[int] = None
    if max_posts is not None:
        try:
            parsed = int(max_posts)
        except (TypeError, ValueError):
            return fail("'max_posts' must be an integer when provided", status=400)
        if parsed >= 1:
            parsed_max_posts = parsed

    if _wants_workflow_stream(body):
        return _stream_workflow(
            "stego",
            lambda emit: runner.run_stego(
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=parsed_list_offset,
                run_all=run_all,
                max_posts=parsed_max_posts,
                on_progress=lambda event, progress_payload: emit(
                    "progress",
                    {"event": event, **progress_payload},
                ),
            ),
        )

    try:
        data = _sync_workflow(
            "stego",
            lambda: runner.run_stego(
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=parsed_list_offset,
                run_all=run_all,
                max_posts=parsed_max_posts,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/decode", methods=["POST"])
def wf_decode() -> Any:
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    stego_text = body.get("stego_text")
    angles = body.get("angles")
    few_shots = body.get("few_shots")
    if not isinstance(stego_text, str):
        return fail("'stego_text' must be a string", status=400)
    if not isinstance(angles, list):
        return fail("'angles' must be a list", status=400)
    if few_shots is not None and not isinstance(few_shots, list):
        return fail("'few_shots' must be a list when provided", status=400)

    if _wants_workflow_stream(body):
        return _stream_workflow(
            "decode",
            lambda emit: {
                "decoded_index": runner.run_decode(
                    stego_text=stego_text,
                    angles=angles,
                    few_shots=few_shots,
                    on_progress=lambda event, payload: emit(
                        "progress",
                        {"event": event, **payload},
                    ),
                )
            },
        )

    try:
        payload_out = _sync_workflow(
            "decode",
            lambda: {
                "decoded_index": runner.run_decode(
                    stego_text=stego_text,
                    angles=angles,
                    few_shots=few_shots,
                )
            },
        )
        return ok(payload_out)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/gen-terms", methods=["POST"])
def wf_gen_terms() -> Any:
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    post_id = body.get("post_id")
    if not isinstance(post_id, str):
        return fail("'post_id' must be a string", status=400)

    if _wants_workflow_stream(body):
        return _stream_workflow(
            "gen-terms",
            lambda emit: runner.run_gen_search_terms(
                post_id=post_id,
                post_title=body.get("post_title"),
                post_text=body.get("post_text"),
                post_url=body.get("post_url"),
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
        )

    try:
        data = _sync_workflow(
            "gen-terms",
            lambda: runner.run_gen_search_terms(
                post_id=post_id,
                post_title=body.get("post_title"),
                post_text=body.get("post_text"),
                post_url=body.get("post_url"),
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/full", methods=["POST"])
def wf_full() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    start_step = str(body.get("start_step", "filter-url-unresolved"))
    count = body.get("count", 1)
    try:
        parsed_count = int(count)
    except (TypeError, ValueError):
        return fail("'count' must be an integer", status=400)
    pipeline_payload, err = _optional_payload_field(body, "payload")
    if err:
        return err

    if _wants_workflow_stream(body):
        return _stream_workflow(
            "full",
            lambda emit: runner.run_full_pipeline(
                start_step=start_step,
                count=parsed_count,
                payload=pipeline_payload,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
        )

    try:
        data = _sync_workflow(
            "full",
            lambda: runner.run_full_pipeline(
                start_step=start_step,
                count=parsed_count,
                payload=pipeline_payload,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/pipelines", methods=["GET"])
def wf_pipelines() -> tuple[Any, int]:
    return ok(
        {
            "commands": list(WORKFLOW_COMMANDS),
            "endpoints": [f"/api/v1/workflows/{name}" for name in WORKFLOW_COMMANDS],
            "generic_run_endpoint": "/api/v1/workflows/run",
            "runs_status_endpoint": "/api/v1/workflows/runs",
        }
    )


@bp.route("/workflows/runs", methods=["GET"])
def wf_runs() -> tuple[Any, int]:
    runs = list(iter_snapshot())
    return ok({"runs": runs, "count": len(runs)})


@bp.route("/workflows/run", methods=["POST"])
def wf_run() -> Any:
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    command = body.get("command")
    if not isinstance(command, str):
        return fail("'command' must be a string", status=400)
    if command not in WORKFLOW_COMMANDS:
        return fail(
            f"Unsupported workflow command: {command}",
            status=400,
            details={"supported_commands": list(WORKFLOW_COMMANDS)},
        )

    execute: Optional[Callable[[Optional[Callable[[str, dict[str, Any]], None]]], Any]] = None

    if command == "data-load":
        count, err = _body_int(body, "count", 100)
        if err:
            return err
        offset, err = _body_int(body, "offset", 0)
        if err:
            return err
        batch_size, err = _body_int(body, "batch_size", 5)
        if err:
            return err
        assert count is not None and offset is not None and batch_size is not None

        def execute(progress_cb: Optional[Callable[[str, dict[str, Any]], None]]) -> Any:
            return runner.run_data_load(
                count=count,
                offset=offset,
                batch_size=batch_size,
                on_progress=progress_cb,
            )

    elif command == "research":
        count, err = _body_int(body, "count", 1)
        if err:
            return err
        offset, err = _body_int(body, "offset", 0)
        if err:
            return err
        assert count is not None and offset is not None

        def execute(progress_cb: Optional[Callable[[str, dict[str, Any]], None]]) -> Any:
            return runner.run_research(count=count, offset=offset, on_progress=progress_cb)

    elif command == "gen-angles":
        count, err = _body_int(body, "count", 1)
        if err:
            return err
        offset, err = _body_int(body, "offset", 0)
        if err:
            return err
        assert count is not None and offset is not None

        def execute(progress_cb: Optional[Callable[[str, dict[str, Any]], None]]) -> Any:
            return runner.run_gen_angles(count=count, offset=offset, on_progress=progress_cb)

    elif command == "stego":
        list_offset, err = _body_int(body, "list_offset", 1)
        if err:
            return err
        run_all, err = _body_bool(body, "run_all", default=False)
        if err:
            return err
        max_posts = body.get("max_posts")
        parsed_max_posts: Optional[int] = None
        if max_posts is not None:
            try:
                parsed = int(max_posts)
            except (TypeError, ValueError):
                return fail("'max_posts' must be an integer when provided", status=400)
            if parsed >= 1:
                parsed_max_posts = parsed
        post_id, err = _optional_body_str(body, "post_id")
        if err:
            return err
        payload, err = _optional_payload_field(body, "payload")
        if err:
            return err
        tag, err = _optional_body_str(body, "tag")
        if err:
            return err
        assert list_offset is not None

        def execute(progress_cb: Optional[Callable[[str, dict[str, Any]], None]]) -> Any:
            return runner.run_stego(
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=list_offset,
                run_all=run_all,
                max_posts=parsed_max_posts,
                on_progress=progress_cb,
            )

    elif command == "decode":
        stego_text = body.get("stego_text")
        angles = body.get("angles")
        few_shots = body.get("few_shots")
        if not isinstance(stego_text, str):
            return fail("'stego_text' must be a string", status=400)
        if not isinstance(angles, list):
            return fail("'angles' must be a list", status=400)
        if few_shots is not None and not isinstance(few_shots, list):
            return fail("'few_shots' must be a list when provided", status=400)

        def execute(progress_cb: Optional[Callable[[str, dict[str, Any]], None]]) -> Any:
            return {
                "decoded_index": runner.run_decode(
                    stego_text=stego_text,
                    angles=angles,
                    few_shots=few_shots,
                    on_progress=progress_cb,
                )
            }

    elif command == "gen-terms":
        post_id = body.get("post_id")
        if not isinstance(post_id, str):
            return fail("'post_id' must be a string", status=400)

        def execute(progress_cb: Optional[Callable[[str, dict[str, Any]], None]]) -> Any:
            return runner.run_gen_search_terms(
                post_id=post_id,
                post_title=body.get("post_title"),
                post_text=body.get("post_text"),
                post_url=body.get("post_url"),
                on_progress=progress_cb,
            )

    else:
        count, err = _body_int(body, "count", 1)
        if err:
            return err
        assert count is not None
        start_step = str(body.get("start_step", "filter-url-unresolved"))
        pipeline_payload, err = _optional_payload_field(body, "payload")
        if err:
            return err

        def execute(progress_cb: Optional[Callable[[str, dict[str, Any]], None]]) -> Any:
            return runner.run_full_pipeline(
                start_step=start_step,
                count=count,
                payload=pipeline_payload,
                on_progress=progress_cb,
            )

    assert execute is not None

    if _wants_workflow_stream(body):
        return _stream_workflow(
            command,
            lambda emit: execute(
                lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                )
            ),
        )

    try:
        data = _sync_workflow(command, lambda: execute(None))
        return ok({"command": command, "result": data})
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/tools/process-file", methods=["POST"])
def tool_process_file() -> tuple[Any, int]:
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    name = body.get("name")
    step = body.get("step")
    if not isinstance(name, str) or not isinstance(step, str):
        return fail("'name' and 'step' must be strings", status=400)
    try:
        return ok(process_post_file(name, step))
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        return fail("Process file failed", status=500, details=str(exc))


@bp.route("/tools/fetch-url", methods=["POST"])
def tool_fetch_url() -> tuple[Any, int]:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    url = str(body.get("url", "")).strip()
    use_crawl4ai = bool(body.get("use_crawl4ai", False))
    try:
        data = fetch_url_content_crawl4ai(url) if use_crawl4ai else fetch_url_content(url)
        return ok(data)
    except Exception as exc:
        return fail("URL fetch failed", status=500, details=str(exc))


@bp.route("/tools/search/news", methods=["GET"])
def tool_search_news() -> tuple[Any, int]:
    query = request.args.get("query") or request.args.get("q")
    if not query:
        return fail("Missing required query parameter: query", status=400)
    try:
        return ok(search_news_api(query))
    except Exception as exc:
        return fail("Search failed", status=500, details=str(exc))


@bp.route("/tools/search/ollama", methods=["GET"])
def tool_search_ollama() -> tuple[Any, int]:
    query = request.args.get("query") or request.args.get("q")
    if not query:
        return fail("Missing required query parameter: query", status=400)
    try:
        return ok({"results": search_ollama(query)})
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        return fail("Search failed", status=500, details=str(exc))


@bp.route("/tools/search/bing", methods=["GET"])
def tool_search_bing() -> tuple[Any, int]:
    query = request.args.get("query")
    if not query:
        return fail("Missing required query parameter: query", status=400)
    first, err = _query_int("first", default=1)
    if err:
        return err
    count, err = _query_int("count", default=10)
    if err:
        return err
    assert first is not None
    assert count is not None
    try:
        return ok(search_bing(query=query, first=first, count=count))
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        return fail("Search failed", status=500, details=str(exc))


@bp.route("/tools/search/google", methods=["GET"])
def tool_search_google() -> tuple[Any, int]:
    query = request.args.get("query")
    if not query:
        return fail("Missing required query parameter: query", status=400)
    first, err = _query_int("first", default=1)
    if err:
        return err
    count, err = _query_int("count", default=10)
    if err:
        return err
    assert first is not None
    assert count is not None
    try:
        return ok(search_google(query=query, first=first, count=count))
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        return fail("Search failed", status=500, details=str(exc))


@bp.route("/tools/semantic/search", methods=["POST"])
def tool_semantic_search() -> tuple[Any, int]:
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    query_text = body.get("text")
    objects_list = body.get("objects")
    n = body.get("n")
    if not isinstance(query_text, str):
        return fail("'text' must be a string", status=400)
    if not isinstance(objects_list, list):
        return fail("'objects' must be a list", status=400)
    try:
        return ok(semantic_search(query_text, objects_list, n))
    except ValueError as exc:
        return fail(str(exc), status=400)
    except ImportError as exc:
        return fail(str(exc), status=500)
    except Exception as exc:
        return fail("Semantic search failed", status=500, details=str(exc))


@bp.route("/tools/semantic/needle", methods=["POST"])
def tool_semantic_needle() -> tuple[Any, int]:
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    needle = body.get("needle")
    haystack = body.get("haystack")
    if not isinstance(needle, str):
        return fail("'needle' must be a string", status=400)
    if not isinstance(haystack, list) or not all(isinstance(item, str) for item in haystack):
        return fail("'haystack' must be a list of strings", status=400)
    try:
        return ok(find_best_match(needle, haystack))
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        return fail("Needle finder failed", status=500, details=str(exc))


@bp.route("/tools/angles/analyze", methods=["POST"])
def tool_angles_analyze() -> tuple[Any, int]:
    body, err = _json_body()
    if err:
        return err
    assert body is not None
    texts = body.get("texts")
    if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
        return fail("'texts' must be a list of strings", status=400)
    try:
        return ok({"results": analyze_angles(texts)})
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        return fail("Angles analysis failed", status=500, details=str(exc))


@bp.route("/kv", methods=["GET"])
def kv_list() -> tuple[Any, int]:
    limit, err = _query_int("limit", default=100)
    if err:
        return err
    offset, err = _query_int("offset", default=0)
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
    body, err = _json_body()
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
