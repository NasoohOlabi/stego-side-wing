"""Compatibility API entrypoint.

This module intentionally stays as `src/API.py` so existing commands keep working:
`uv run python src/API.py`.
"""
from __future__ import annotations

import argparse
import os

from app.app_factory import create_app


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the stego-side-wing API")
    parser.add_argument("--dev", action="store_true", help="Run in development mode")
    parser.add_argument("--host", default=os.environ.get("API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("API_PORT", "5001")))
    return parser.parse_args()


def main() -> None:
    """Run the canonical Flask app."""
    args = _parse_args()
    app = create_app()
    dev_mode = args.dev or _is_truthy(os.environ.get("API_DEBUG"))
    app.run(host=args.host, port=args.port, debug=dev_mode, use_reloader=dev_mode)


if __name__ == "__main__":
    main()
