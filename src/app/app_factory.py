"""Flask application factory."""
import atexit

from flask import Flask
from flask_caching import Cache
from flask_cors import CORS

from infrastructure.event_loop import start_event_loop, stop_event_loop

# Suppress LiteLLM debug info to reduce verbose logging
try:
    import litellm

    litellm.suppress_debug_info = True
except ImportError:
    pass  # litellm may not be directly imported, but used by crawl4ai


def create_app() -> Flask:
    """
    Create and configure Flask application.
    
    Returns:
        Configured Flask app instance
    """
    app = Flask(__name__)
    CORS(app)
    
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
    
    return app
