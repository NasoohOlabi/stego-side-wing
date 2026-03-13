"""CLI entry point for running workflow pipelines."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# Allow running as: uv run python src/scripts/workflow_cli.py ...
REPO_SRC = Path(__file__).resolve().parents[1]
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from workflows.runner import WorkflowRunner  # noqa: E402


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _read_json_file(path: str) -> Any:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_angles(path: str) -> List[Dict[str, Any]]:
    data = _read_json_file(path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("angles"), list):
        return data["angles"]
    raise ValueError("angles file must be a JSON list or object with an 'angles' list")


def _load_optional_json_list(path: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None
    data = _read_json_file(path)
    if not isinstance(data, list):
        raise ValueError("few-shots file must be a JSON list")
    return data


def _print_result(result: Any) -> None:
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _print_stego_result(result: Dict[str, Any], verbose: bool = False) -> None:
    if verbose:
        _print_result(result)
        return

    compact = {
        "succeeded": result.get("succeeded"),
        "retry_count": result.get("retry_count"),
        "tag": result.get("tag"),
        "angle_index": result.get("angle_index"),
        "selected_angle": result.get("selected_angle"),
        "stego_text": result.get("stego_text"),
        "post_id": (result.get("post") or {}).get("id") if isinstance(result.get("post"), dict) else None,
        "error": result.get("error"),
        "error_details": result.get("error_details"),
        "validation_details": result.get("validation_details"),
    }
    _print_result(compact)


def _resolve_log_path(log_file: str) -> Path:
    """Resolve log file path and ensure parent directory exists."""
    path = Path(log_file).expanduser()
    if not path.is_absolute():
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _configure_logging(level: str, log_file: str, log_console: bool) -> Path:
    log_path = _resolve_log_path(log_file)
    handlers: List[logging.Handler] = [
        logging.FileHandler(log_path, encoding="utf-8"),
    ]
    if log_console:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    for noisy_logger in (
        "httpx",
        "httpcore",
        "urllib3",
        "sentence_transformers",
        "transformers",
        "huggingface_hub",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    return log_path


def _add_data_load_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("data-load", help="Run DataLoad pipeline")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=5)


def _add_research_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("research", help="Run Research pipeline")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)


def _add_gen_angles_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("gen-angles", help="Run GenAngles pipeline")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)


def _add_stego_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("stego", help="Run Stego pipeline")
    parser.add_argument("--post-id", default=None, help="Optional post ID (without .json)")
    parser.add_argument(
        "--payload",
        default=None,
        help=(
            "Secret payload to encode; if omitted, use SetSecretData payload "
            "from workflows/27rZrYtywu3k9e7Q.json"
        ),
    )
    parser.add_argument("--tag", default=None, help="Optional output tag")
    parser.add_argument(
        "--list-offset",
        type=int,
        default=1,
        help="Offset when auto-selecting next unprocessed post",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full stego result JSON (includes large fields)",
    )


def _add_decode_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("decode", help="Run Decode pipeline")
    parser.add_argument("--stego-text", required=True, help="Stego text to decode")
    parser.add_argument(
        "--angles-file",
        required=True,
        help="Path to JSON list of angles or post JSON containing 'angles'",
    )
    parser.add_argument(
        "--few-shots-file",
        default=None,
        help="Optional path to JSON list of few-shot examples",
    )


def _add_gen_terms_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "gen-terms",
        help="Generate search terms from post content",
    )
    parser.add_argument("--post-id", required=True)
    parser.add_argument("--post-title", default=None)
    parser.add_argument("--post-text", default=None)
    parser.add_argument("--post-url", default=None)


def _add_full_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("full", help="Run full workflow pipeline")
    parser.add_argument("--start-step", default="filter-url-unresolved")
    parser.add_argument("--count", type=int, default=1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run stego-side-wing workflows from CLI",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=LOG_LEVELS,
        help="Set runtime logging verbosity",
    )
    parser.add_argument(
        "--log-file",
        default="logs/workflow_cli.log",
        help="Write logs to this file path (default: logs/workflow_cli.log)",
    )
    parser.add_argument(
        "--log-console",
        action="store_true",
        help="Also print logs to console (default: file-only logging)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_data_load_parser(subparsers)
    _add_research_parser(subparsers)
    _add_gen_angles_parser(subparsers)
    _add_stego_parser(subparsers)
    _add_decode_parser(subparsers)
    _add_gen_terms_parser(subparsers)
    _add_full_parser(subparsers)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _configure_logging(args.log_level, args.log_file, args.log_console)
    runner = WorkflowRunner()

    try:
        if args.command == "data-load":
            result = runner.run_data_load(
                count=args.count,
                offset=args.offset,
                batch_size=args.batch_size,
            )
        elif args.command == "research":
            result = runner.run_research(count=args.count, offset=args.offset)
        elif args.command == "gen-angles":
            result = runner.run_gen_angles(count=args.count, offset=args.offset)
        elif args.command == "stego":
            result = runner.run_stego(
                post_id=args.post_id,
                payload=args.payload,
                tag=args.tag,
                list_offset=args.list_offset,
            )
        elif args.command == "decode":
            angles = _load_angles(args.angles_file)
            few_shots = _load_optional_json_list(args.few_shots_file)
            result = {
                "decoded_index": runner.run_decode(
                    stego_text=args.stego_text,
                    angles=angles,
                    few_shots=few_shots,
                )
            }
        elif args.command == "gen-terms":
            result = runner.run_gen_search_terms(
                post_id=args.post_id,
                post_title=args.post_title,
                post_text=args.post_text,
                post_url=args.post_url,
            )
        elif args.command == "full":
            result = runner.run_full_pipeline(
                start_step=args.start_step,
                count=args.count,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "stego" and isinstance(result, dict):
        _print_stego_result(result, verbose=args.verbose)
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
