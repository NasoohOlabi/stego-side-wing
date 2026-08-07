#!/usr/bin/env python3
"""Offline capacity preflight for POLYCARRIER Phase 1 knobs.

Uses the same recoverable-capacity estimate as
``scripts/run_multi_frame_batch_e2e._batch_capacity_bits`` (no LLM). Exit 0 only
when the first ``--samples`` full batches each have capacity >= useful bits +
control margin.

Example::

    uv run python scripts/polycarrier_capacity_preflight.py \\
      --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \\
      --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32
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
from workflows.utils.stego_codec import selection_channel_capacity_report  # noqa: E402

_LOG = logger.bind(component="PolycarrierCapacityPreflight")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--angles-dir", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--posts-per-sample", type=int, default=6)
    parser.add_argument("--max-frames-per-post", type=int, default=4)
    parser.add_argument("--payload-bytes", type=int, default=32)
    parser.add_argument("--control-bit-margin", type=int, default=32)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else _REPO_ROOT / path


def _load_posts(angles_dir: Path) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    for path in sorted(angles_dir.glob("*.json")):
        post = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(post, dict) and post.get("angles"):
            posts.append(post)
    return posts


def _post_capacity_bits(post: dict[str, Any], max_frames: int) -> int:
    report = selection_channel_capacity_report(post)
    if int(report["comment_choices"]) <= 0:
        return 0
    return int(report["recoverable_capacity_bits"]) * max(0, max_frames)


def _batch_capacity(batch: list[dict[str, Any]], max_frames: int) -> int:
    return sum(_post_capacity_bits(post, max_frames) for post in batch)


def _full_batches(
    posts: list[dict[str, Any]], posts_per_sample: int, samples: int
) -> list[list[dict[str, Any]]]:
    chunks = [
        posts[i : i + posts_per_sample]
        for i in range(0, len(posts), posts_per_sample)
        if len(posts[i : i + posts_per_sample]) == posts_per_sample
    ]
    return chunks[:samples]


def main() -> int:
    args = _parse_args()
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    angles_dir = _resolve(Path(args.angles_dir))
    posts = _load_posts(angles_dir)
    need = args.payload_bytes * 8 + args.control_bit_margin
    batches = _full_batches(posts, args.posts_per_sample, args.samples)
    _LOG.info(
        "preflight_start",
        angles_dir=str(angles_dir),
        posts=len(posts),
        batches=len(batches),
        need_bits=need,
        posts_per_sample=args.posts_per_sample,
        max_frames_per_post=args.max_frames_per_post,
    )
    if len(batches) < args.samples:
        _LOG.error(
            "insufficient_full_batches",
            have=len(batches),
            want=args.samples,
            posts=len(posts),
        )
        return 1
    failures = 0
    for index, batch in enumerate(batches):
        capacity = _batch_capacity(batch, args.max_frames_per_post)
        fit = capacity >= need
        failures += 0 if fit else 1
        _LOG.info(
            "sample_capacity",
            sample_index=index,
            capacity_bits=capacity,
            need_bits=need,
            fit=fit,
            post_ids=[post.get("id") for post in batch],
        )
    if failures:
        _LOG.error("preflight_failed", failing_samples=failures, need_bits=need)
        return 1
    _LOG.info("preflight_ok", samples=len(batches), need_bits=need)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
