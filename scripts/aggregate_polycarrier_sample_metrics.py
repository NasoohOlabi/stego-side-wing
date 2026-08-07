#!/usr/bin/env python3
"""Aggregate POLYCARRIER sample-layer metrics from a multi-frame run.

Reads ours ``summary.json`` (``records[]`` / ``entries[]``) and optionally
``paired_rows.jsonl`` from a capacity-matched ZLG comparison build, then writes
sample-level rollups defined in ``docs/plans/polycarrier/metrics-multicomment.md``.

Example::

    uv run python scripts/aggregate_polycarrier_sample_metrics.py \\
      --source-summary metrics/e2e_runs/polycarrier_256b_smoke/summary.json \\
      --paired-rows metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke/comparison_dataset/paired_rows.jsonl \\
      --dataset-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \\
      --output metrics/e2e_runs/polycarrier_256b_smoke/sample_layer_metrics.json
"""

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

from loguru import logger  # noqa: E402

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from services.polycarrier_sample_metrics import (  # noqa: E402
    build_sample_layer_report,
    parse_paired_rows,
    parse_summary_records,
)

_LOG = logger.bind(component="PolycarrierSampleMetrics")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else _REPO_ROOT / path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-summary", required=True)
    parser.add_argument("--paired-rows", default=None)
    parser.add_argument(
        "--dataset-dir",
        default=None,
        help="Angles/posts dir for concatenated KL/JSD baselines (optional).",
    )
    parser.add_argument("--alpha", type=float, default=1e-6)
    parser.add_argument(
        "--cluster-by-primary-post",
        action="store_true",
        help="Cluster sample-layer paired stats by primary_post_id (metrics §7).",
    )
    parser.add_argument("--output", default=None)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def _default_output(summary_path: Path) -> Path:
    return summary_path.parent / "sample_layer_metrics.json"


def main() -> int:
    args = _parse_args()
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    summary_path = _resolve(Path(args.source_summary))
    summary = _load_json(summary_path)
    records = parse_summary_records(summary)
    frame_rows = []
    if args.paired_rows:
        paired_path = _resolve(Path(args.paired_rows))
        frame_rows = parse_paired_rows(_load_jsonl(paired_path))
        _LOG.info("paired_rows_loaded", path=str(paired_path), n=len(frame_rows))
    dataset_dir = _resolve(Path(args.dataset_dir)) if args.dataset_dir else None
    report = build_sample_layer_report(
        records,
        frame_rows,
        dataset_dir=dataset_dir,
        alpha=args.alpha,
        cluster_by_primary_post=args.cluster_by_primary_post,
    )
    out_path = _resolve(Path(args.output)) if args.output else _default_output(summary_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    payload["source_summary"] = str(summary_path.resolve())
    if args.paired_rows:
        payload["paired_rows"] = str(_resolve(Path(args.paired_rows)).resolve())
    if dataset_dir is not None:
        payload["dataset_dir"] = str(dataset_dir.resolve())
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _LOG.info(
        "sample_layer_written",
        output=str(out_path),
        samples=report.samples_attempted,
        end_to_end_ok=report.samples_end_to_end_ok,
        success_rate=report.ours_end_to_end_success_rate,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
