"""Search API routes."""
from flask import Blueprint, jsonify, make_response, request
from flask_caching import Cache

from app.schemas.validators import get_query_param
from services.search_service import search_bing, search_google, search_news_api, search_ollama

bp = Blueprint("search", __name__)


def get_cache() -> Cache:
    """Get cache instance from app config."""
    from flask import current_app
    cache = current_app.config.get("cache")
    if cache is None:
        raise RuntimeError("Cache not initialized in app config")
    return cache


@bp.route("/search", methods=["GET"])
def search():
    """Deprecated News API search endpoint."""
    q, err = get_query_param("q", str, required=True)
    if err:
        return err
    assert q is not None
    
    try:
        result = search_news_api(q)
        if "error" in result:
            return jsonify(result["error"]), 500
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/ollama_search", methods=["GET"])
def ollama_search():
    """Search using Ollama web search."""
    cache = get_cache()
    
    @cache.cached(query_string=True)
    def _cached_search(q: str):
        return search_ollama(q)
    
    q, err = get_query_param("q", str, required=True)
    if err:
        return err
    assert q is not None
    
    try:
        results = _cached_search(q)
        return jsonify(results), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/bing_search", methods=["GET"])
def bing_search():
    """Proxy endpoint that wraps ScrapingDog Bing search API."""
    cache = get_cache()
    
    @cache.cached(timeout=300, query_string=True)
    def _cached_search(query: str, first: int, count: int):
        return search_bing(query, first, count)
    
    query, err = get_query_param("query", str, required=True)
    if err:
        return err
    assert query is not None
    
    first, _ = get_query_param("first", int, required=False, default=1)
    count, _ = get_query_param("count", int, required=False, default=10)
    assert first is not None
    assert count is not None
    
    try:
        result = _cached_search(query, first, count)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/google_search", methods=["GET"])
def google_search():
    """Proxy endpoint that wraps Google Custom Search API."""
    import hashlib
    from urllib.parse import parse_qsl, urlencode
    
    cache = get_cache()
    
    # Generate cache key
    endpoint = request.endpoint or "google_search"
    func_name = "google_search"
    base_key = f"view/{endpoint}.{func_name}"
    
    query_params = dict(parse_qsl(request.query_string.decode("utf-8")))
    if query_params:
        sorted_params = sorted(query_params.items())
        query_str = urlencode(sorted_params)
        query_hash = hashlib.md5(query_str.encode("utf-8")).hexdigest()
        cache_key = f"{base_key}?{query_hash}"
    else:
        cache_key = base_key
    
    # Check cache first
    cached_value = cache.get(cache_key)
    if cached_value is not None:
        if isinstance(cached_value, tuple):
            if len(cached_value) == 2:
                response_obj = make_response(cached_value[0], cached_value[1])
            elif len(cached_value) == 3:
                response_obj = make_response(cached_value[0], cached_value[1], cached_value[2])
            else:
                response_obj = make_response(cached_value[0])
        else:
            response_obj = make_response(cached_value)
        response_obj.headers["X-Cache-Status"] = "HIT"
        return response_obj
    
    query, err = get_query_param("query", str, required=True)
    if err:
        response = jsonify({"error": "Missing 'query' parameter"}), 400
        response_obj = make_response(response[0], response[1])
        response_obj.headers["X-Cache-Status"] = "MISS"
        return response_obj
    assert query is not None
    
    first, _ = get_query_param("first", int, required=False, default=1)
    count, _ = get_query_param("count", int, required=False, default=10)
    assert first is not None
    assert count is not None
    
    try:
        result = search_google(query, first, count)
        response = (jsonify(result), 200)
        # Cache successful response
        cache.set(cache_key, response, timeout=300)
        response_obj = make_response(response[0], response[1])
        response_obj.headers["X-Cache-Status"] = "MISS"
        return response_obj
    except ValueError as e:
        response = (jsonify({"error": str(e)}), 500)
        response_obj = make_response(response[0], response[1])
        response_obj.headers["X-Cache-Status"] = "MISS"
        return response_obj
