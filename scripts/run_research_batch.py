from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from workflows.runner import WorkflowRunner  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Grow the researched-post pool by running data-load (URL resolution) then research "
            "(live web search enrichment) on posts from the news_cleaned seed corpus that are "
            "not yet in news_url_fetched / news_researched (for the resolved WORKFLOW_DATASET_ROOT, "
            "if set). Both stages resume automatically: posts already present in a stage's "
            "destination dir are skipped by the underlying posts_list query, so rerunning with "
            "--offset 0 continues where a previous invocation left off."
        )
    )
    parser.add_argument(
        "--count",
        type=int,
        required=True,
        help="How many news_cleaned posts to attempt data-load for (offset paginates this pool).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset into news_cleaned (size-descending) for data-load. Failed fetches are not "
        "saved anywhere, so a retry of the same range re-attempts the same failures -- advance "
        "this past prior attempts' --offset + --count to reach fresh posts.",
    )
    parser.add_argument(
        "--research-count",
        type=int,
        default=0,
        help="How many freshly-fetched posts to research. Defaults to --count. Research's own "
        "offset is always 0: its source pool is whatever this run's data-load just added to "
        "news_url_fetched, not the global news_cleaned range --offset paginates.",
    )
    parser.add_argument("--data-load-batch-size", type=int, default=5)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--skip-data-load",
        action="store_true",
        help="Skip the data-load stage (use when the target posts already have URLs resolved).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    runner = WorkflowRunner()

    data_load_results: list[dict] = []
    if not args.skip_data_load:
        data_load_results = runner.run_data_load(
            count=args.count,
            offset=args.offset,
            batch_size=args.data_load_batch_size,
        )

    research_results = runner.run_research(
        count=args.research_count or args.count, offset=0
    )

    summary = {
        "requested_count": args.count,
        "offset": args.offset,
        "data_load_processed_count": len(data_load_results),
        "research_processed_count": len(research_results),
        "research_post_ids": [post.get("id") for post in research_results],
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    append_current_pid_to_log()
    main()
