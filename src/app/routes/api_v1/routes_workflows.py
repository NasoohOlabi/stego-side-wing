"""API v1: workflow POST routes and generic ``/workflows/run``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import request
from pydantic import ValidationError

from app.routes.api_v1.blueprint import bp
from app.routes.api_v1.constants import WORKFLOW_COMMANDS
from app.routes.api_v1.http_parsers import (
    body_bool,
    body_int,
    json_body,
    optional_body_str,
    optional_payload_field,
    query_int,
    required_body_str,
)
from app.routes.api_v1.runner_access import runner
from app.routes.api_v1.workflow_streaming import (
    stream_workflow,
    sync_workflow,
    wants_workflow_stream,
)
from app.schemas.responses import fail, ok
from app.schemas.workflow_requests import (
    DecodeWorkflowRequest,
    ReceiverWorkflowRequest,
    ValidatePostWorkflowRequest,
)
from infrastructure.json_logging import get_trace_id
from infrastructure.workflow_run_tracker import has_active_run_for_command, iter_snapshot
from services.double_process_history import list_double_process_runs
from services.workflow_facade import (
    reconcile_stale_double_process_claim_vs_explicit,
    try_read_double_process_claim,
)

logger = logging.getLogger(__name__)


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

    if wants_workflow_stream(body):
        return stream_workflow(
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
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
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
    include_breakdown, inc_err = body_bool(body, "include_breakdown", default=False)
    if inc_err:
        return inc_err

    if wants_workflow_stream(body):
        return stream_workflow(
            "research",
            lambda emit: runner.run_research(
                count=parsed_count,
                offset=parsed_offset,
                include_breakdown=include_breakdown,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "research",
            lambda: runner.run_research(
                count=parsed_count,
                offset=parsed_offset,
                include_breakdown=include_breakdown,
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
    tag, err = optional_body_str(body, "tag")
    if err:
        return err

    if wants_workflow_stream(body):
        return stream_workflow(
            "gen-angles",
            lambda emit: runner.run_gen_angles(
                count=parsed_count,
                offset=parsed_offset,
                tag=tag,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "gen-angles",
            lambda: runner.run_gen_angles(
                count=parsed_count,
                offset=parsed_offset,
                tag=tag,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/prep-until-google-quota-then-stego", methods=["POST"])
def wf_prep_until_google_quota_then_stego() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    tag, err = required_body_str(body, "tag")
    if err:
        return err
    batch_count, err = body_int(body, "batch_count", 1)
    if err:
        return err
    batch_size, err = body_int(body, "batch_size", 5)
    if err:
        return err
    payload, err = optional_payload_field(body, "payload")
    if err:
        return err
    assert tag is not None and batch_count is not None and batch_size is not None

    if wants_workflow_stream(body):
        return stream_workflow(
            "prep-until-google-quota-then-stego",
            lambda emit: runner.run_prep_until_google_quota_then_stego(
                tag=tag,
                batch_count=batch_count,
                batch_size=batch_size,
                payload=payload,
                on_progress=lambda event, progress_payload: emit(
                    "progress",
                    {"event": event, **progress_payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "prep-until-google-quota-then-stego",
            lambda: runner.run_prep_until_google_quota_then_stego(
                tag=tag,
                batch_count=batch_count,
                batch_size=batch_size,
                payload=payload,
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
    post_id, err = optional_body_str(body, "post_id")
    if err:
        return err
    payload, err = optional_payload_field(body, "payload")
    if err:
        return err
    tag, err = optional_body_str(body, "tag")
    if err:
        return err
    list_offset = body.get("list_offset", 1)
    try:
        parsed_list_offset = int(list_offset)
    except (TypeError, ValueError):
        return fail("'list_offset' must be an integer", status=400)
    run_all, err = body_bool(body, "run_all", default=False)
    if err:
        return err
    max_posts = body.get("max_posts")
    parsed_max_posts: int | None = None
    if max_posts is not None:
        try:
            parsed = int(max_posts)
        except (TypeError, ValueError):
            return fail("'max_posts' must be an integer when provided", status=400)
        if parsed >= 1:
            parsed_max_posts = parsed

    if wants_workflow_stream(body):
        return stream_workflow(
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
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
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
    body, err = json_body()
    if err:
        return err
    assert body is not None
    try:
        req = DecodeWorkflowRequest.model_validate(body)
    except ValidationError as exc:
        return fail("Invalid request body", status=400, details=exc.errors())
    stego_text = req.stego_text
    angles = req.angles
    few_shots = req.few_shots
    strict_mode = req.strict_mode

    if wants_workflow_stream(body):
        return stream_workflow(
            "decode",
            lambda emit: {
                "decoded_index": runner.run_decode(
                    stego_text=stego_text,
                    angles=angles,
                    few_shots=few_shots,
                    strict_mode=strict_mode,
                    on_progress=lambda event, payload: emit(
                        "progress",
                        {"event": event, **payload},
                    ),
                )
            },
            trace_id=get_trace_id(),
        )

    try:
        payload_out = sync_workflow(
            "decode",
            lambda: {
                "decoded_index": runner.run_decode(
                    stego_text=stego_text,
                    angles=angles,
                    few_shots=few_shots,
                    strict_mode=strict_mode,
                )
            },
        )
        return ok(payload_out)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/receiver", methods=["POST"])
def wf_receiver() -> Any:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    try:
        req = ReceiverWorkflowRequest.model_validate(body)
    except ValidationError as exc:
        return fail("Invalid request body", status=400, details=exc.errors())
    post = req.post
    sender_user_id = req.sender_user_id
    compressed_full = req.compressed_bitstring
    allow_fallback = req.allow_fallback
    fail_on_context_drift = req.fail_on_context_drift
    strict_decode = req.strict_decode
    use_fetch_cache = req.use_fetch_cache
    use_terms_cache = req.use_terms_cache
    persist_terms_cache = req.persist_terms_cache
    use_fetch_cache_research = req.use_fetch_cache_research
    max_padding_bits = req.max_padding_bits

    if wants_workflow_stream(body):
        return stream_workflow(
            "receiver",
            lambda emit: runner.run_receiver(
                post,
                sender_user_id,
                use_fetch_cache=use_fetch_cache,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache_research=use_fetch_cache_research,
                allow_fallback=allow_fallback,
                compressed_full=compressed_full,
                max_padding_bits=max_padding_bits,
                fail_on_context_drift=fail_on_context_drift,
                strict_decode=strict_decode,
                on_progress=lambda event, progress_payload: emit(
                    "progress",
                    {"event": event, **progress_payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "receiver",
            lambda: runner.run_receiver(
                post,
                sender_user_id,
                use_fetch_cache=use_fetch_cache,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache_research=use_fetch_cache_research,
                allow_fallback=allow_fallback,
                compressed_full=compressed_full,
                max_padding_bits=max_padding_bits,
                fail_on_context_drift=fail_on_context_drift,
                strict_decode=strict_decode,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/stego-receiver-live", methods=["POST"])
def wf_stego_receiver_live() -> Any:
    """Run stego then receiver with disjoint sender/receiver disk caches (live-like)."""
    body, err = json_body()
    if err:
        return err
    assert body is not None
    sender_user_id, err = required_body_str(body, "sender_user_id")
    if err:
        return err
    assert sender_user_id is not None
    post_id, err = optional_body_str(body, "post_id")
    if err:
        return err
    payload, err = optional_payload_field(body, "payload")
    if err:
        return err
    tag, err = optional_body_str(body, "tag")
    if err:
        return err
    list_offset = body.get("list_offset", 1)
    try:
        parsed_list_offset = int(list_offset)
    except (TypeError, ValueError):
        return fail("'list_offset' must be an integer", status=400)
    sim_root_raw, err = optional_body_str(body, "simulation_root")
    if err:
        return err
    simulation_root = Path(sim_root_raw).resolve() if sim_root_raw else None
    compressed_full, err = optional_body_str(body, "compressed_bitstring")
    if err:
        return err
    allow_fallback, err = body_bool(body, "allow_fallback", default=False)
    if err:
        return err
    max_pad = body.get("max_padding_bits", 256)
    try:
        max_padding_bits = int(max_pad)
    except (TypeError, ValueError):
        return fail("'max_padding_bits' must be an integer when provided", status=400)
    if max_padding_bits < 0:
        return fail("'max_padding_bits' must be non-negative", status=400)
    max_post_attempts = body.get("max_post_attempts", 25)
    try:
        parsed_max_post_attempts = int(max_post_attempts)
    except (TypeError, ValueError):
        return fail("'max_post_attempts' must be an integer when provided", status=400)
    if parsed_max_post_attempts < 1:
        return fail("'max_post_attempts' must be at least 1", status=400)

    if wants_workflow_stream(body):
        return stream_workflow(
            "stego-receiver-live",
            lambda emit: runner.run_stego_receiver_live_sim(
                sender_user_id,
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=parsed_list_offset,
                simulation_root=simulation_root,
                max_post_attempts=parsed_max_post_attempts,
                allow_fallback=allow_fallback,
                compressed_full=compressed_full,
                max_padding_bits=max_padding_bits,
                on_progress=lambda event, progress_payload: emit(
                    "progress",
                    {"event": event, **progress_payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "stego-receiver-live",
            lambda: runner.run_stego_receiver_live_sim(
                sender_user_id,
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=parsed_list_offset,
                simulation_root=simulation_root,
                max_post_attempts=parsed_max_post_attempts,
                allow_fallback=allow_fallback,
                compressed_full=compressed_full,
                max_padding_bits=max_padding_bits,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/gen-terms", methods=["POST"])
def wf_gen_terms() -> Any:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    post_id = body.get("post_id")
    if not isinstance(post_id, str):
        return fail("'post_id' must be a string", status=400)

    if wants_workflow_stream(body):
        return stream_workflow(
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
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
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


@bp.route("/workflows/validate-post", methods=["POST"])
def wf_validate_post() -> Any:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    try:
        req = ValidatePostWorkflowRequest.model_validate(body)
    except ValidationError as exc:
        return fail("Invalid request body", status=400, details=exc.errors())
    post_id = req.post_id
    use_terms_cache = req.use_terms_cache
    persist_terms_cache = req.persist_terms_cache
    use_fetch_cache = req.use_fetch_cache
    allow_angles_fallback = req.allow_angles_fallback

    if wants_workflow_stream(body):
        return stream_workflow(
            "validate-post",
            lambda emit: runner.validate_post_pipeline(
                post_id=post_id,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache=use_fetch_cache,
                allow_angles_fallback=allow_angles_fallback,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "validate-post",
            lambda: runner.validate_post_pipeline(
                post_id=post_id,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache=use_fetch_cache,
                allow_angles_fallback=allow_angles_fallback,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/double-process-new-post", methods=["POST"])
def wf_double_process_new_post() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    allow_angles_fallback, err = body_bool(body, "allow_angles_fallback", default=False)
    if err:
        return err
    explicit_post_id, err = optional_body_str(body, "post_id")
    if err:
        return err
    if explicit_post_id:
        reconcile_stale_double_process_claim_vs_explicit(
            explicit_post_id,
            has_active_double_process_run=has_active_run_for_command("double-process-new-post"),
        )
        claimed = try_read_double_process_claim()
        if claimed and claimed[0] != explicit_post_id:
            return fail(
                "Active double-process claim exists for a different post_id; "
                "finish that run or clear the claim before targeting another post.",
                status=400,
                details={"claim_post_id": claimed[0], "requested_post_id": explicit_post_id},
            )

    if wants_workflow_stream(body):
        return stream_workflow(
            "double-process-new-post",
            lambda emit: runner.run_double_process_new_post(
                allow_angles_fallback=allow_angles_fallback,
                explicit_post_id=explicit_post_id,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "double-process-new-post",
            lambda: runner.run_double_process_new_post(
                allow_angles_fallback=allow_angles_fallback,
                explicit_post_id=explicit_post_id,
            ),
        )
        return ok(data)
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))


@bp.route("/workflows/batch-angles-determinism", methods=["POST"])
def wf_batch_angles_determinism() -> Any:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return fail("Invalid JSON body", status=400)
    raw_ids = body.get("post_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return fail("'post_ids' must be a non-empty list of strings", status=400)
    post_ids: list[str] = []
    for x in raw_ids:
        if not isinstance(x, str) or not x.strip():
            return fail("'post_ids' entries must be non-empty strings", status=400)
        post_ids.append(x.strip())
    step = body.get("step", "angles-step")
    if not isinstance(step, str) or not step.strip():
        return fail("'step' must be a non-empty string", status=400)
    step = step.strip()

    if wants_workflow_stream(body):
        return stream_workflow(
            "batch-angles-determinism",
            lambda emit: runner.run_batch_angles_determinism(
                post_ids,
                step=step,
                on_progress=lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                ),
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
            "batch-angles-determinism",
            lambda: runner.run_batch_angles_determinism(post_ids, step=step),
        )
        return ok(data)
    except ValueError as exc:
        return fail(str(exc), status=400)
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
    pipeline_payload, err = optional_payload_field(body, "payload")
    if err:
        return err

    if wants_workflow_stream(body):
        return stream_workflow(
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
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(
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


@bp.route("/workflows/double-process-posts", methods=["GET"])
def wf_double_process_posts() -> Any:
    raw_post = request.args.get("post_id")
    post_id = raw_post.strip() if isinstance(raw_post, str) and raw_post.strip() else None
    limit_raw, err = query_int("limit", default=50)
    if err:
        return err
    assert limit_raw is not None
    limit = max(1, min(limit_raw, 100))
    try:
        result = list_double_process_runs(post_id=post_id, limit=limit)
    except ValidationError as exc:
        return fail("Invalid query parameters", status=400, details=exc.errors())
    return ok(result.model_dump())


@bp.route("/workflows/run", methods=["POST"])
def wf_run() -> Any:
    body, err = json_body()
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

    run_dispatch: Callable[[Callable[[str, dict[str, Any]], None] | None], Any] | None = None

    if command == "data-load":
        count, err = body_int(body, "count", 100)
        if err:
            return err
        offset, err = body_int(body, "offset", 0)
        if err:
            return err
        batch_size, err = body_int(body, "batch_size", 5)
        if err:
            return err
        assert count is not None and offset is not None and batch_size is not None

        def _run_data_load(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_data_load(
                count=count,
                offset=offset,
                batch_size=batch_size,
                on_progress=progress_cb,
            )

        run_dispatch = _run_data_load

    elif command == "research":
        count, err = body_int(body, "count", 1)
        if err:
            return err
        offset, err = body_int(body, "offset", 0)
        if err:
            return err
        assert count is not None and offset is not None
        include_breakdown, inc_err = body_bool(body, "include_breakdown", default=False)
        if inc_err:
            return inc_err

        def _run_research(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_research(
                count=count,
                offset=offset,
                on_progress=progress_cb,
                include_breakdown=include_breakdown,
            )

        run_dispatch = _run_research

    elif command == "gen-angles":
        count, err = body_int(body, "count", 1)
        if err:
            return err
        offset, err = body_int(body, "offset", 0)
        if err:
            return err
        tag, err = optional_body_str(body, "tag")
        if err:
            return err
        assert count is not None and offset is not None

        def _run_gen_angles(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_gen_angles(
                count=count, offset=offset, tag=tag, on_progress=progress_cb
            )

        run_dispatch = _run_gen_angles

    elif command == "prep-until-google-quota-then-stego":
        tag, err = required_body_str(body, "tag")
        if err:
            return err
        batch_count, err = body_int(body, "batch_count", 1)
        if err:
            return err
        batch_size, err = body_int(body, "batch_size", 5)
        if err:
            return err
        payload, err = optional_payload_field(body, "payload")
        if err:
            return err
        assert tag is not None and batch_count is not None and batch_size is not None

        def _run_prep_until_google_quota_then_stego(
            progress_cb: Callable[[str, dict[str, Any]], None] | None,
        ) -> Any:
            return runner.run_prep_until_google_quota_then_stego(
                tag=tag,
                batch_count=batch_count,
                batch_size=batch_size,
                payload=payload,
                on_progress=progress_cb,
            )

        run_dispatch = _run_prep_until_google_quota_then_stego

    elif command == "stego":
        list_offset, err = body_int(body, "list_offset", 1)
        if err:
            return err
        run_all, err = body_bool(body, "run_all", default=False)
        if err:
            return err
        max_posts = body.get("max_posts")
        parsed_max_posts: int | None = None
        if max_posts is not None:
            try:
                parsed = int(max_posts)
            except (TypeError, ValueError):
                return fail("'max_posts' must be an integer when provided", status=400)
            if parsed >= 1:
                parsed_max_posts = parsed
        post_id, err = optional_body_str(body, "post_id")
        if err:
            return err
        payload, err = optional_payload_field(body, "payload")
        if err:
            return err
        tag, err = optional_body_str(body, "tag")
        if err:
            return err
        assert list_offset is not None

        def _run_stego(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_stego(
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=list_offset,
                run_all=run_all,
                max_posts=parsed_max_posts,
                on_progress=progress_cb,
            )

        run_dispatch = _run_stego

    elif command == "decode":
        stego_text = body.get("stego_text")
        angles = body.get("angles")
        few_shots = body.get("few_shots")
        strict_mode, err = body_bool(body, "strict_mode", default=False)
        if err:
            return err
        if not isinstance(stego_text, str):
            return fail("'stego_text' must be a string", status=400)
        if not isinstance(angles, list):
            return fail("'angles' must be a list", status=400)
        if few_shots is not None and not isinstance(few_shots, list):
            return fail("'few_shots' must be a list when provided", status=400)

        def _run_decode(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return {
                "decoded_index": runner.run_decode(
                    stego_text=stego_text,
                    angles=angles,
                    few_shots=few_shots,
                    strict_mode=strict_mode,
                    on_progress=progress_cb,
                )
            }

        run_dispatch = _run_decode

    elif command == "receiver":
        post_obj = body.get("post")
        if not isinstance(post_obj, dict):
            return fail("'post' must be an object", status=400)
        sender_uid, err = required_body_str(body, "sender_user_id")
        if err:
            return err
        assert sender_uid is not None
        compressed_b, err = optional_body_str(body, "compressed_bitstring")
        if err:
            return err
        allow_fb, err = body_bool(body, "allow_fallback", default=False)
        if err:
            return err
        fail_drift, err = body_bool(body, "fail_on_context_drift", default=True)
        if err:
            return err
        strict_dec, err = body_bool(body, "strict_decode", default=False)
        if err:
            return err
        ufc, err = body_bool(body, "use_fetch_cache", default=True)
        if err:
            return err
        utc, err = body_bool(body, "use_terms_cache", default=True)
        if err:
            return err
        ptc, err = body_bool(body, "persist_terms_cache", default=True)
        if err:
            return err
        ufcr, err = body_bool(body, "use_fetch_cache_research", default=True)
        if err:
            return err
        max_pad_b = body.get("max_padding_bits", 256)
        try:
            max_pad_i = int(max_pad_b)
        except (TypeError, ValueError):
            return fail("'max_padding_bits' must be an integer when provided", status=400)
        if max_pad_i < 0:
            return fail("'max_padding_bits' must be non-negative", status=400)

        def _run_receiver(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_receiver(
                post_obj,
                sender_uid,
                use_fetch_cache=ufc,
                use_terms_cache=utc,
                persist_terms_cache=ptc,
                use_fetch_cache_research=ufcr,
                allow_fallback=allow_fb,
                compressed_full=compressed_b,
                max_padding_bits=max_pad_i,
                fail_on_context_drift=fail_drift,
                strict_decode=strict_dec,
                on_progress=progress_cb,
            )

        run_dispatch = _run_receiver

    elif command == "gen-terms":
        post_id = body.get("post_id")
        if not isinstance(post_id, str):
            return fail("'post_id' must be a string", status=400)

        def _run_gen_terms(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_gen_search_terms(
                post_id=post_id,
                post_title=body.get("post_title"),
                post_text=body.get("post_text"),
                post_url=body.get("post_url"),
                on_progress=progress_cb,
            )

        run_dispatch = _run_gen_terms

    elif command == "validate-post":
        post_id, err = required_body_str(body, "post_id")
        if err:
            return err
        use_terms_cache, err = body_bool(body, "use_terms_cache", default=False)
        if err:
            return err
        persist_terms_cache, err = body_bool(body, "persist_terms_cache", default=False)
        if err:
            return err
        use_fetch_cache, err = body_bool(body, "use_fetch_cache", default=False)
        if err:
            return err
        allow_angles_fallback, err = body_bool(body, "allow_angles_fallback", default=False)
        if err:
            return err
        assert post_id is not None

        def _run_validate_post(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.validate_post_pipeline(
                post_id=post_id,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache=use_fetch_cache,
                allow_angles_fallback=allow_angles_fallback,
                on_progress=progress_cb,
            )

        run_dispatch = _run_validate_post

    elif command == "double-process-new-post":
        allow_angles_fallback, err = body_bool(body, "allow_angles_fallback", default=False)
        if err:
            return err
        explicit_post_id_cmd, err = optional_body_str(body, "post_id")
        if err:
            return err
        if explicit_post_id_cmd:
            reconcile_stale_double_process_claim_vs_explicit(
                explicit_post_id_cmd,
                has_active_double_process_run=has_active_run_for_command("double-process-new-post"),
            )
            claimed_cmd = try_read_double_process_claim()
            if claimed_cmd and claimed_cmd[0] != explicit_post_id_cmd:
                return fail(
                    "Active double-process claim exists for a different post_id; "
                    "finish that run or clear the claim before targeting another post.",
                    status=400,
                    details={
                        "claim_post_id": claimed_cmd[0],
                        "requested_post_id": explicit_post_id_cmd,
                    },
                )

        def _run_double_process(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_double_process_new_post(
                allow_angles_fallback=allow_angles_fallback,
                explicit_post_id=explicit_post_id_cmd,
                on_progress=progress_cb,
            )

        run_dispatch = _run_double_process

    elif command == "batch-angles-determinism":
        raw_ids = body.get("post_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return fail("'post_ids' must be a non-empty list of strings", status=400)
        post_ids_cmd: list[str] = []
        for x in raw_ids:
            if not isinstance(x, str) or not x.strip():
                return fail("'post_ids' entries must be non-empty strings", status=400)
            post_ids_cmd.append(x.strip())
        step_val = body.get("step", "angles-step")
        if not isinstance(step_val, str) or not step_val.strip():
            return fail("'step' must be a non-empty string", status=400)
        step_val = step_val.strip()

        def _run_batch_angles(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_batch_angles_determinism(
                post_ids_cmd,
                step=step_val,
                on_progress=progress_cb,
            )

        run_dispatch = _run_batch_angles

    elif command == "stego-receiver-live":
        live_sender_user_id, err = required_body_str(body, "sender_user_id")
        if err:
            return err
        assert live_sender_user_id is not None
        live_post_id, err = optional_body_str(body, "post_id")
        if err:
            return err
        live_payload, err = optional_payload_field(body, "payload")
        if err:
            return err
        live_tag, err = optional_body_str(body, "tag")
        if err:
            return err
        live_list_offset, err = body_int(body, "list_offset", 1)
        if err:
            return err
        assert live_list_offset is not None
        live_sim_root_raw, err = optional_body_str(body, "simulation_root")
        if err:
            return err
        live_simulation_root = Path(live_sim_root_raw).resolve() if live_sim_root_raw else None
        live_compressed_full, err = optional_body_str(body, "compressed_bitstring")
        if err:
            return err
        live_allow_fallback, err = body_bool(body, "allow_fallback", default=False)
        if err:
            return err
        live_max_padding_bits, err = body_int(body, "max_padding_bits", 256)
        if err:
            return err
        assert live_max_padding_bits is not None
        if live_max_padding_bits < 0:
            return fail("'max_padding_bits' must be non-negative", status=400)
        live_max_post_attempts, err = body_int(body, "max_post_attempts", 25)
        if err:
            return err
        assert live_max_post_attempts is not None
        if live_max_post_attempts < 1:
            return fail("'max_post_attempts' must be at least 1", status=400)

        def _run_stego_receiver_live(
            progress_cb: Callable[[str, dict[str, Any]], None] | None,
        ) -> Any:
            return runner.run_stego_receiver_live_sim(
                live_sender_user_id,
                post_id=live_post_id,
                payload=live_payload,
                tag=live_tag,
                list_offset=live_list_offset,
                simulation_root=live_simulation_root,
                max_post_attempts=live_max_post_attempts,
                allow_fallback=live_allow_fallback,
                compressed_full=live_compressed_full,
                max_padding_bits=live_max_padding_bits,
                on_progress=progress_cb,
            )

        run_dispatch = _run_stego_receiver_live

    else:
        # Only "full" reaches here now. Every other entry in WORKFLOW_COMMANDS has an
        # explicit branch above, and unknown commands were rejected with 400 already.
        count, err = body_int(body, "count", 1)
        if err:
            return err
        assert count is not None
        start_step = str(body.get("start_step", "filter-url-unresolved"))
        pipeline_payload, err = optional_payload_field(body, "payload")
        if err:
            return err

        def _run_full(progress_cb: Callable[[str, dict[str, Any]], None] | None) -> Any:
            return runner.run_full_pipeline(
                start_step=start_step,
                count=count,
                payload=pipeline_payload,
                on_progress=progress_cb,
            )

        run_dispatch = _run_full

    assert run_dispatch is not None

    if wants_workflow_stream(body):
        return stream_workflow(
            command,
            lambda emit: run_dispatch(
                lambda event, payload: emit(
                    "progress",
                    {"event": event, **payload},
                )
            ),
            trace_id=get_trace_id(),
        )

    try:
        data = sync_workflow(command, lambda: run_dispatch(None))
        return ok({"command": command, "result": data})
    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))
