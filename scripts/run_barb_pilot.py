#!/usr/bin/env python3
"""Thin pilot wrapper: balanced vs barb under metrics/experiments/barb/runs/<run_id>/."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from run_actual_workload_e2e import run_actual_workload_e2e  # noqa: E402

from infrastructure.config import override_workflow_context_sampler  # noqa: E402
from infrastructure.json_logging import configure_api_logging  # noqa: E402

BARB_RUNS_ROOT = _REPO_ROOT / "metrics" / "experiments" / "barb" / "runs"
DEFAULT_VARIANTS = ("balanced", "barb")
DEFAULT_SAMPLES = 25
DEFAULT_SAMPLER = "context_weighted_v2"


def _default_run_id() -> str:
    return f"barb_pilot_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"


def _resolve_run_dir(run_id: str, run_dir: Path | None) -> Path:
    resolved = (
        run_dir.resolve() if run_dir is not None else (BARB_RUNS_ROOT / run_id).resolve()
    )
    if not str(resolved).startswith(str(BARB_RUNS_ROOT.resolve())):
        raise SystemExit(f"BARB runs must stay under {BARB_RUNS_ROOT}, got {resolved}")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BARB vs balanced pilot e2e.")
    parser.add_argument("--run-id", default=_default_run_id())
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--angles-dir", type=Path, default=_REPO_ROOT / "datasets" / "news_angles")
    parser.add_argument(
        "--dataset-dir", type=Path, default=_REPO_ROOT / "datasets" / "news_cleaned"
    )
    parser.add_argument("--post-id", action="append", default=[])
    parser.add_argument("--context-sampler", default=DEFAULT_SAMPLER)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    resolved = _resolve_run_dir(args.run_id, args.run_dir)
    with override_workflow_context_sampler(args.context_sampler):
        summary = run_actual_workload_e2e(
            profiles=("balanced",),
            variants=DEFAULT_VARIANTS,
            samples_per_profile=args.samples,
            post_ids=args.post_id,
            angles_dir=args.angles_dir,
            dataset_dir=args.dataset_dir,
            run_dir=resolved,
            overwrite=bool(args.overwrite),
            max_retries=args.max_retries,
            force_model_generation=True,
            skip_receiver_decode=False,
            allow_post_reuse=False,
            fail_fast=False,
        )
    print(f"BARB pilot complete: {summary.get('run_dir', resolved)}")


if __name__ == "__main__":
    main()
