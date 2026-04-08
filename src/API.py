"""Compatibility API entrypoint.

This module intentionally stays as `src/API.py` so existing commands keep working:
`uv run python src/API.py`.
"""

from __future__ import annotations

import argparse
import logging
import os

from loguru import logger

from app.app_factory import create_app
from infrastructure.json_logging import log_process_start


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the stego-side-wing API")
    parser.add_argument("--dev", action="store_true", help="Run in development mode")
    parser.add_argument("--host", default=os.environ.get("API_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("API_PORT", "5001"))
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("API_LOG_LEVEL") or None,
        metavar="LEVEL",
        help="Logging level (default: INFO or API_LOG_LEVEL)",
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("API_LOG_FILE") or None,
        metavar="PATH",
        help="JSONL log file path (default: logs/api.jsonl under repo root)",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="Log only to stderr (no logs/api.jsonl)",
    )
    return parser.parse_args()


def main() -> None:
    """Run the canonical Flask app."""
    args = _parse_args()
    app = create_app(
        log_level=args.log_level,
        log_file=args.log_file,
        enable_file_log=not args.no_log_file,
    )
    dev_mode = args.dev or _is_truthy(os.environ.get("API_DEBUG"))
    logger.bind(component="APIEntry").info(
        "api_cli_startup",
        dev_mode=dev_mode,
        host=args.host,
        port=args.port,
        log_level_set=bool(args.log_level),
        log_file_set=bool(args.log_file),
        file_logging_enabled=not args.no_log_file,
    )
    log_process_start(
        logging.getLogger("app"),
        "api_server",
        host=args.host,
        port=args.port,
        debug=dev_mode,
        use_reloader=dev_mode,
    )
    app.run(host=args.host, port=args.port, debug=dev_mode, use_reloader=dev_mode)


if __name__ == "__main__":
    # Windows: UTF-8 for stdout/stderr so crawl4ai and other libs can print symbols safely.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
