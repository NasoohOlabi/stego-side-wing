"""Run isolated baseline-vs-naturalness-gate benchmark batches."""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loguru import logger  # noqa: E402
from run_actual_workload_e2e import (  # noqa: E402
    NATURALNESS_EXPERIMENT_ROOT,
    has_usable_angles,
    read_json,
    run_actual_workload_e2e,
)

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402


def _select_random_post_ids(
    *,
    angles_dir: Path,
    dataset_dir: Path,
    count: int,
    seed: int,
) -> list[str]:
    candidates: list[str] = []
    for path in sorted(angles_dir.glob("*.json")):
        if not (dataset_dir / path.name).is_file():
            continue
        try:
            post = read_json(path)
        except Exception:
            continue
        if has_usable_angles(post):
            candidates.append(path.stem)
    if len(candidates) < count:
        raise ValueError(f"Only found {len(candidates)} usable posts, need {count}.")
    rng = random.Random(seed)
    return rng.sample(candidates, count)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run naturalness-gate baseline and gated benchmark in an isolated folder."
    )
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260511)
    parser.add_argument("--angles-dir", default=str(_REPO_ROOT / "datasets" / "news_angles"))
    parser.add_argument("--dataset-dir", default=str(_REPO_ROOT / "datasets" / "news_cleaned"))
    parser.add_argument("--experiment-root", default=str(NATURALNESS_EXPERIMENT_ROOT))
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--skip-receiver-decode", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    experiment_root = Path(args.experiment_root).resolve()
    run_name = args.run_name or f"naturalness_gate_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = experiment_root / "runs" / run_name
    logs_dir = experiment_root / "logs"
    reviews_dir = experiment_root / "reviews"
    logs_dir.mkdir(parents=True, exist_ok=True)
    reviews_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        logs_dir / f"{run_name}.progress.jsonl",
        level="INFO",
        serialize=True,
        filter=lambda record: record["extra"].get("component") == "ActualWorkloadE2E",
    )

    angles_dir = Path(args.angles_dir).resolve()
    dataset_dir = Path(args.dataset_dir).resolve()
    post_ids = _select_random_post_ids(
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        count=args.samples,
        seed=args.seed,
    )
    run_actual_workload_e2e(
        profiles=("balanced",),
        variants=("balanced", "balanced_naturalness_gate"),
        samples_per_profile=args.samples,
        post_ids=post_ids,
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        run_dir=run_dir,
        overwrite=bool(args.overwrite),
        max_retries=args.max_retries,
        force_model_generation=True,
        skip_receiver_decode=bool(args.skip_receiver_decode),
        allow_post_reuse=False,
        fail_fast=False,
    )


if __name__ == "__main__":
    append_current_pid_to_log()
    main()
