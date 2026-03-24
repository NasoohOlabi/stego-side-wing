"""Flask application factory."""
import atexit
import logging
import time
import uuid

from flask import Flask, g, request
from flask_caching import Cache
from flask_cors import CORS

from infrastructure.config import REPO_ROOT
from infrastructure.event_loop import start_event_loop, stop_event_loop
from infrastructure.json_logging import (
    TAG_HTTP,
    TAG_LIFECYCLE,
    TAG_PROCESS,
    bind_trace_id,
    configure_api_logging,
    reset_trace_id,
)
from infrastructure.stdio_utf8 import configure_stdio_utf8

# Suppress LiteLLM debug info to reduce verbose logging
try:
    import litellm

    litellm.suppress_debug_info = True
except ImportError:
    pass  # litellm may not be directly imported, but used by crawl4ai


def create_app(
    *,
    log_level: str | None = None,
    log_file: str | None = None,
    enable_file_log: bool = True,
) -> Flask:
    """
    Create and configure Flask application.

    Returns:
        Configured Flask app instance
    """
    configure_stdio_utf8()
    configure_api_logging(
        level=log_level if log_level is not None else "",
        log_file=log_file,
        enable_file_log=enable_file_log,
        repo_root=REPO_ROOT,
    )
    log = logging.getLogger("app")
    app = Flask(__name__)
    CORS(app)

    @app.before_request
    def _api_assign_request_id() -> None:
        rid = str(uuid.uuid4())
        g._api_request_id = rid
        g._api_request_t0 = time.perf_counter()
        g._trace_ctx_token = bind_trace_id(rid)

    @app.after_request
    def _api_log_access(response):  # type: ignore[no-untyped-def]
        t0 = getattr(g, "_api_request_t0", None)
        duration_ms = None
        if t0 is not None:
            duration_ms = round((time.perf_counter() - t0) * 1000, 3)
        logging.getLogger("app.http").info(
            "http_access",
            extra={
                "event": "http_access",
                "tags": [TAG_HTTP, TAG_LIFECYCLE],
                "request_id": getattr(g, "_api_request_id", None),
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "blueprint": request.blueprint,
                "endpoint": request.endpoint,
            },
        )
        tok = getattr(g, "_trace_ctx_token", None)
        if tok is not None:
            reset_trace_id(tok)
        return response
    
    # Start the persistent event loop at module level
    # This ensures it's available before any requests are handled
    start_event_loop()
    
    # Configure Flask-Caching
    cache = Cache(
        config={
            "CACHE_TYPE": "FileSystemCache",  # Store on disk, not RAM
            "CACHE_DIR": "cache-directory",  # Folder name (will be created auto)
            "CACHE_DEFAULT_TIMEOUT": 9999999,  # ~115 days (effectively permanent)
            "CACHE_THRESHOLD": 10000,  # Max number of items to store
        }
    )
    cache.init_app(app)
    app.config["cache"] = cache
    
    # Register blueprints
    from app.routes import (
        api_v1_routes,
        analysis_routes,
        angles_routes,
        kv_routes,
        posts_routes,
        search_routes,
        semantic_routes,
    )
    
    app.register_blueprint(api_v1_routes.bp)
    app.register_blueprint(posts_routes.bp)
    app.register_blueprint(search_routes.bp)
    app.register_blueprint(analysis_routes.bp)
    app.register_blueprint(semantic_routes.bp)
    app.register_blueprint(angles_routes.bp)
    app.register_blueprint(kv_routes.bp)
    
    # Register root route
    @app.route("/", methods=["GET"])
    def index():
        """Simple welcome message for the API root."""
        return (
            "Welcome to stego-side-wing API. "
            "Use /api/v1/health and /api/v1/state/steps for the versioned API surface."
        )
    
    # Cleanup on app shutdown
    atexit.register(stop_event_loop)

    log.info(
        "app_ready",
        extra={
            "event": "app_startup",
            "tags": [TAG_PROCESS, TAG_LIFECYCLE],
            "component": "flask",
            "repo_root": str(REPO_ROOT),
        },
    )

    return app
