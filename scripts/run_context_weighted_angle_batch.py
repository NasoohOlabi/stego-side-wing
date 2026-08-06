from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.config import (  # noqa: E402
    override_step_dirs,
    override_workflow_context_sampler,
)
from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from workflows.runner import WorkflowRunner  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate angles for already-researched posts under an isolated dataset root, "
            "reading from --source-researched-dir and writing to --output-angles-dir. It never "
            "touches either source directory and does not perform Google Search. "
            "Posts already present (by tag) in news_angles are skipped automatically by the "
            "underlying posts_list query, so an interrupted batch can resume by rerunning with "
            "the same --tag and --offset 0 -- offset only paginates the still-unprocessed queue."
        )
    )
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--tag", default=None)
    parser.add_argument(
        "--context-sampler",
        choices=("context_weighted_v2", "post_level_v1"),
        default="context_weighted_v2",
        help="Angle-input sampler for this run; defaults to the LUCID sampler.",
    )
    parser.add_argument(
        "--source-researched-dir",
        type=Path,
        required=True,
        help="Existing cached researched-post directory to read (for example datasets/news_researched).",
    )
    parser.add_argument(
        "--output-angles-dir",
        type=Path,
        required=True,
        help="New isolated directory for regenerated angle artifacts (for example datasets/prep_runs/LUCID/news_angles).",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    source = args.source_researched_dir.resolve()
    destination = args.output_angles_dir.resolve()
    if not source.is_dir():
        raise ValueError(f"--source-researched-dir does not exist or is not a directory: {source}")
    if source == destination:
        raise ValueError("--output-angles-dir must differ from --source-researched-dir")
    destination.mkdir(parents=True, exist_ok=True)
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    with (
        override_step_dirs({"angles-step": (source, destination)}),
        override_workflow_context_sampler(args.context_sampler),
    ):
        runner = WorkflowRunner()
        results = runner.run_gen_angles(count=args.count, offset=args.offset, tag=args.tag)
    summary = {
        "requested_count": args.count,
        "offset": args.offset,
        "source_researched_dir": str(source),
        "output_angles_dir": str(destination),
        "context_sampler": args.context_sampler,
        "processed_count": len(results),
        "post_ids": [post.get("id") for post in results],
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    append_current_pid_to_log()
    main()
