from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loguru import logger  # noqa: E402

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from infrastructure.workflow_run_tracker import track_workflow  # noqa: E402
from workflows.runner import WorkflowRunner  # noqa: E402
from workflows.utils.prep_run_manifest import write_prep_run_manifest  # noqa: E402


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
    parser.add_argument(
        "--dataset-root",
        help="Isolated prep-run root (absolute or repo-relative).",
    )
    parser.add_argument(
        "--prep-run-id",
        help="Manifest run ID; defaults to the dataset-root directory name.",
    )
    parser.add_argument("--notes", default="", help="Optional prep-run manifest notes.")
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
    if args.dataset_root:
        os.environ["WORKFLOW_DATASET_ROOT"] = args.dataset_root
        run_id = args.prep_run_id or Path(args.dataset_root).name
        manifest_path = write_prep_run_manifest(run_id=run_id, notes=args.notes)
        logger.bind(component="PrepUntilQuotaThenStego").info(
            "prep_run_manifest_written",
            manifest_path=str(manifest_path),
            run_id=run_id,
        )
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
    append_current_pid_to_log()
    main()
