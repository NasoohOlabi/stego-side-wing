from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from loguru import logger  # noqa: E402
from services.workflow_run_tracker import track_workflow  # noqa: E402
from workflows.runner import WorkflowRunner  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run data_load -> research -> gen_angles until Google Search quota is hit, "
            "then continue stego generation using the supplied tag. If prep runs out "
            "of candidate posts before quota is hit, the command exits without stego."
        )
    )
    parser.add_argument("--tag", required=True, help="Tag appended to generated stego samples.")
    parser.add_argument(
        "--payload",
        default=None,
        help="Optional payload override. Defaults to the workflow payload when omitted.",
    )
    parser.add_argument(
        "--batch-count",
        type=int,
        default=1,
        help="Number of posts to attempt per prep iteration.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Data-load sub-batch size used inside each prep iteration.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="JSONL log level.",
    )
    return parser.parse_args()


def _log_progress(event: str, payload: dict[str, Any]) -> None:
    logger.bind(component="PrepUntilQuotaThenStego").info(
        "prep_until_quota_progress",
        progress_event=event,
        progress_payload=payload,
    )


def main() -> None:
    args = _parse_args()
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    runner = WorkflowRunner()
    with track_workflow("prep-until-google-quota-then-stego"):
        result = runner.run_prep_until_google_quota_then_stego(
            tag=args.tag,
            batch_count=args.batch_count,
            batch_size=args.batch_size,
            payload=args.payload,
            on_progress=_log_progress,
        )
    logger.bind(component="PrepUntilQuotaThenStego").info(
        "prep_until_quota_complete",
        tag=args.tag,
        result=result,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
