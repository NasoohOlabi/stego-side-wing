"""API v1: tools (fetch, metrics, search, semantic, angles, protocol previews)."""
from __future__ import annotations

import logging
from typing import Any

from flask import request

from app.routes.api_v1.blueprint import bp
from app.routes.api_v1.http_parsers import (
    body_bool,
    body_int,
    body_metrics_dataset_dir,
    body_metrics_dir,
    body_metrics_output_basename,
    body_metrics_output_dir,
    json_body,
    preview_response,
    query_int,
    query_metrics_dir_param,
    required_body_str,
)
from app.routes.api_v1.runner_access import runner
from app.schemas.responses import fail, ok
from services.analysis_service import (
    fetch_url_content,
    fetch_url_content_crawl4ai,
    process_post_file,
)
from services.angles_service import analyze_angles
from services.search_service import search_bing, search_google, search_news_api, search_ollama
from services.semantic_service import find_best_match, semantic_search

logger = logging.getLogger(__name__)

@bp.route("/tools/process-file", methods=["POST"])
def tool_process_file() -> tuple[Any, int]:
    body, err = json_body()
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


@bp.route("/tools/metrics/perplexity", methods=["POST"])
def tool_metrics_perplexity() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    output_dir, err = body_metrics_output_dir(body)
    if err:
        return err
    assert output_dir is not None
    metrics_dir, err = body_metrics_dir(body)
    if err:
        return err
    assert metrics_dir is not None
    model_raw = body.get("model_name", "gpt2")
    if not isinstance(model_raw, str) or not model_raw.strip():
        return fail("'model_name' must be a non-empty string", status=400)
    stride, err = body_int(body, "stride", 512)
    if err:
        return err
    assert stride is not None
    if stride <= 0:
        return fail("'stride' must be a positive integer", status=400)
    device_raw = body.get("device", "auto")
    if not isinstance(device_raw, str) or device_raw not in ("auto", "cpu", "cuda"):
        return fail("'device' must be one of: auto, cpu, cuda", status=400)
    try:
        from app.routes import api_v1_routes as ar

        data = ar.run_perplexity_metrics(
            output_dir,
            metrics_dir,
            model_name=model_raw.strip(),
            stride=stride,
            device=device_raw,
            progress_hook=None,
        )
        return ok(data)
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)
    except ImportError as exc:
        return fail(str(exc), status=501)
    except Exception as exc:
        logger.exception("Perplexity metrics failed")
        return fail("Perplexity metrics failed", status=500, details=str(exc))


@bp.route("/tools/metrics/divergence", methods=["POST"])
def tool_metrics_divergence() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    output_dir, err = body_metrics_output_dir(body)
    if err:
        return err
    assert output_dir is not None
    dataset_dir, err = body_metrics_dataset_dir(body)
    if err:
        return err
    assert dataset_dir is not None
    metrics_dir, err = body_metrics_dir(body)
    if err:
        return err
    assert metrics_dir is not None
    alpha_raw = body.get("alpha", 1e-6)
    try:
        alpha = float(alpha_raw)
    except (TypeError, ValueError):
        return fail("'alpha' must be a number", status=400)
    if alpha <= 0:
        return fail("'alpha' must be positive", status=400)
    try:
        from app.routes import api_v1_routes as ar

        data = ar.run_divergence_metrics(
            output_dir,
            dataset_dir,
            metrics_dir,
            alpha=alpha,
            progress_hook=None,
        )
        return ok(data)
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        logger.exception("Divergence metrics failed")
        return fail("Divergence metrics failed", status=500, details=str(exc))


@bp.route("/tools/metrics/post", methods=["POST"])
def tool_metrics_single_post() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    basename, err = body_metrics_output_basename(body)
    if err:
        return err
    assert basename is not None
    output_dir, err = body_metrics_output_dir(body)
    if err:
        return err
    assert output_dir is not None
    dataset_dir, err = body_metrics_dataset_dir(body)
    if err:
        return err
    assert dataset_dir is not None
    model_raw = body.get("model_name", "gpt2")
    if not isinstance(model_raw, str) or not model_raw.strip():
        return fail("'model_name' must be a non-empty string", status=400)
    stride, err = body_int(body, "stride", 512)
    if err:
        return err
    assert stride is not None
    if stride <= 0:
        return fail("'stride' must be a positive integer", status=400)
    device_raw = body.get("device", "auto")
    if not isinstance(device_raw, str) or device_raw not in ("auto", "cpu", "cuda"):
        return fail("'device' must be one of: auto, cpu, cuda", status=400)
    alpha_raw = body.get("alpha", 1e-6)
    try:
        alpha = float(alpha_raw)
    except (TypeError, ValueError):
        return fail("'alpha' must be a number", status=400)
    if alpha <= 0:
        return fail("'alpha' must be positive", status=400)
    output_file = output_dir / basename
    try:
        from app.routes import api_v1_routes as ar

        data = ar.run_single_post_metrics(
            output_file,
            dataset_dir,
            model_name=model_raw.strip(),
            stride=stride,
            device=device_raw,
            alpha=alpha,
            progress_hook=None,
        )
        return ok(data)
    except FileNotFoundError as exc:
        return fail(str(exc), status=404)
    except ValueError as exc:
        return fail(str(exc), status=400)
    except Exception as exc:
        logger.exception("Single-post metrics failed")
        return fail("Single-post metrics failed", status=500, details=str(exc))


@bp.route("/tools/metrics/history", methods=["GET"])
def tool_metrics_history() -> tuple[Any, int]:
    type_raw = (request.args.get("type") or "all").strip().lower()
    if type_raw not in ("all", "perplexity", "divergence"):
        return fail("'type' query must be all, perplexity, or divergence", status=400)
    limit, err = query_int("limit", default=50)
    if err:
        return err
    assert limit is not None
    safe_limit = max(1, min(limit, 200))
    metrics_dir, err = query_metrics_dir_param()
    if err:
        return err
    assert metrics_dir is not None
    try:
        from app.routes import api_v1_routes as ar

        items = ar.list_metrics_history(
            metrics_dir,
            kind_filter=type_raw,
            limit=safe_limit,
        )
    except ValueError as exc:
        return fail(str(exc), status=400)
    return ok(
        {
            "metrics_dir": str(metrics_dir.resolve()),
            "type": type_raw,
            "limit": safe_limit,
            "count": len(items),
            "history": items,
        }
    )


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
    first, err = query_int("first", default=1)
    if err:
        return err
    count, err = query_int("count", default=10)
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
    first, err = query_int("first", default=1)
    if err:
        return err
    count, err = query_int("count", default=10)
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
    body, err = json_body()
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
    body, err = json_body()
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
    body, err = json_body()
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


@bp.route("/tools/protocol/gen-terms", methods=["POST"])
def tool_protocol_gen_terms() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    post_id, err = required_body_str(body, "post_id")
    if err:
        return err
    use_cache, err = body_bool(body, "use_cache", default=False)
    if err:
        return err
    persist_cache, err = body_bool(body, "persist_cache", default=False)
    if err:
        return err
    file_name = f"{post_id}.json"
    source_post = runner.backend.get_post_local(file_name, "filter-url-unresolved")
    assert post_id is not None
    report = runner.gen_terms.preview_generation(
        post_id=post_id,
        post_title=body.get("post_title") or source_post.get("title"),
        post_text=body.get("post_text") or source_post.get("selftext") or source_post.get("text"),
        post_url=body.get("post_url") or source_post.get("url"),
        use_cache=use_cache,
        persist_cache=persist_cache,
    )
    return ok(report)


@bp.route("/tools/protocol/data-load-preview", methods=["POST"])
def tool_protocol_data_load_preview() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
    post_id, err = required_body_str(body, "post_id")
    if err:
        return err
    use_cache, err = body_bool(body, "use_cache", default=False)
    if err:
        return err
    include_post, err = body_bool(body, "include_post", default=False)
    if err:
        return err
    assert post_id is not None
    try:
        preview = runner.preview_data_load_post(post_id=post_id, use_cache=use_cache)
        return ok(preview_response(preview, include_post=include_post))
    except Exception as exc:
        return fail("Data-load preview failed", status=500, details=str(exc))


@bp.route("/tools/protocol/research-preview", methods=["POST"])
def tool_protocol_research_preview() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
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
    include_post, err = body_bool(body, "include_post", default=False)
    if err:
        return err
    assert post_id is not None
    try:
        data_load_preview = runner.preview_data_load_post(
            post_id=post_id,
            use_cache=use_fetch_cache,
        )
        if not data_load_preview["report"].get("fetch_success"):
            return ok(
                {
                    "post_id": post_id,
                    "data_load": preview_response(data_load_preview, include_post=include_post),
                    "research": None,
                }
            )
        research_preview = runner.preview_research_post(
            post_id=post_id,
            source_post=data_load_preview["post"],
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )
        return ok(
            {
                "post_id": post_id,
                "data_load": preview_response(data_load_preview, include_post=include_post),
                "research": preview_response(research_preview, include_post=include_post),
            }
        )
    except Exception as exc:
        return fail("Research preview failed", status=500, details=str(exc))


@bp.route("/tools/protocol/angles-preview", methods=["POST"])
def tool_protocol_angles_preview() -> tuple[Any, int]:
    body, err = json_body()
    if err:
        return err
    assert body is not None
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
    include_post, err = body_bool(body, "include_post", default=False)
    if err:
        return err
    assert post_id is not None
    try:
        data_load_preview = runner.preview_data_load_post(
            post_id=post_id,
            use_cache=use_fetch_cache,
        )
        if not data_load_preview["report"].get("fetch_success"):
            return ok(
                {
                    "post_id": post_id,
                    "data_load": preview_response(data_load_preview, include_post=include_post),
                    "research": None,
                    "gen_angles": None,
                }
            )
        research_preview = runner.preview_research_post(
            post_id=post_id,
            source_post=data_load_preview["post"],
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )
        angles_preview = None
        if not research_preview["report"].get("error"):
            angles_preview = runner.preview_gen_angles_post(
                post_id=post_id,
                source_post=research_preview["post"],
                allow_fallback=allow_angles_fallback,
            )
        return ok(
            {
                "post_id": post_id,
                "data_load": preview_response(data_load_preview, include_post=include_post),
                "research": preview_response(research_preview, include_post=include_post),
                "gen_angles": preview_response(angles_preview, include_post=include_post)
                if angles_preview
                else None,
            }
        )
    except Exception as exc:
        return fail("Angles preview failed", status=500, details=str(exc))

