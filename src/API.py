"""
Compatibility entrypoint for API.py.

This module maintains backward compatibility by importing the new app factory
and exposing the same Flask app instance.
"""
from app.app_factory import create_app

# Create the app instance
app = create_app()

# Expose app for direct import compatibility
__all__ = ["app"]

if __name__ == "__main__":
    import os
    from infrastructure.config import POSTS_DIRECTORY
    from services.kv_service import init_db, migrate_json_to_sqlite
    
    # Migrate data from old JSON file to SQLite if it exists
    migrate_json_to_sqlite()
    # Initialize database (creates table if it doesn't exist)
    init_db()
    print(f"Serving posts from directory: {os.path.abspath(POSTS_DIRECTORY)}")
    print("Starting server...")
    try:
        app.run(host="192.168.100.136", port=5001, debug=False)
    finally:
        from infrastructure.event_loop import stop_event_loop
        stop_event_loop()
