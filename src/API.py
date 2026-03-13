"""Compatibility API entrypoint.

This module intentionally stays as `src/API.py` so existing commands keep working:
`uv run python src/API.py`.
"""
from __future__ import annotations

import os

from app.app_factory import create_app


def main() -> None:
    """Run the canonical Flask app."""
    app = create_app()
    host = os.environ.get("API_HOST", "192.168.100.136")
    port = int(os.environ.get("API_PORT", "5001"))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
