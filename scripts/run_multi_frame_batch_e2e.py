"""Batch multi-frame stego runs over real prepared posts.

Unlike ``run_actual_workload_e2e.py`` (which calls the single-frame
``StegoPipeline.encode``), this harness drives ``encode_payload_frames`` so the
parent-conditioned tangent codebook of ``context_weighted_v2`` is actually exercised.
It emits per-frame output files plus a ``summary.json`` in the ``entries[]`` shape that
``scripts/run_zlg_batch_comparison.py`` consumes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loguru import logger  # noqa: E402

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from workflows.pipelines.receiver import ReceiverPipeline  # noqa: E402
from workflows.pipelines.stego import StegoPipeline  # noqa: E402
from workflows.utils.comment_context import (  # noqa: E402
    ancestor_chain,
    index_comment_tree,
    normalize_thing_id,
)
from workflows.utils.protocol_utils import stable_hash  # noqa: E402
from workflows.utils.stego_codec import selection_channel_capacity_report  # noqa: E402

_LOG = logger.bind(component="MultiFrameBatchE2E")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--angles-dir", required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--posts-per-sample", type=int, default=1)
    parser.add_argument("--max-frames-per-post", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--payload", default=None)
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=0,
        help="Fixed payload size. Default 0 auto-sizes to the batch's recoverable capacity.",
    )
    parser.add_argument(
        "--control-bit-margin",
        type=int,
        default=32,
        help="Bits reserved for Elias-gamma control fields and transform overhead.",
    )
    parser.add_argument("--skip-receiver-decode", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def _load_posts(angles_dir: Path) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    for path in sorted(angles_dir.glob("*.json")):
        post = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(post, dict) and post.get("angles"):
            posts.append(post)
    return posts


def _batch_capacity_bits(batch: list[dict[str, Any]], max_frames_per_post: int) -> int:
    """Total recoverable bits the planner can draw on for this batch."""
    total = 0
    for post in batch:
        report = selection_channel_capacity_report(post)
        if int(report["comment_choices"]) > 0:
            total += int(report["recoverable_capacity_bits"]) * max(0, max_frames_per_post)
    return total


def _sample_payload(batch: list[dict[str, Any]], sample_index: int, args: argparse.Namespace) -> str:
    """Build a payload that fits the batch, since the planner needs the whole payload to fit."""
    if args.payload:
        return args.payload
    if args.payload_bytes > 0:
        size = args.payload_bytes
    else:
        usable = _batch_capacity_bits(batch, args.max_frames_per_post) - args.control_bit_margin
        size = max(1, usable // 8)
    seed = f"mf:{sample_index:04d}:{batch[0].get('id')}:"
    return (seed * (size // len(seed) + 1))[:size]


def _chain_bodies(post: dict[str, Any], parent_id: str | None) -> list[dict[str, str]]:
    """Selected parent then nearest-first ancestors, as ZLG cover rows."""
    if not parent_id:
        return []
    index = index_comment_tree(post)
    normalized = normalize_thing_id(parent_id)
    if normalized is None or normalized not in index:
        return []
    nodes = [index[normalized]]
    nodes.extend(ancestor_chain(index, normalized, normalize_thing_id(post.get("id"))))
    return [{"name": node.comment_id, "body": node.text} for node in nodes if node.text]


def _context_block(post: dict[str, Any]) -> dict[str, Any]:
    """Post fields the Artifact Explorer Result tab expects (URL, search, etc.)."""
    block: dict[str, Any] = {
        "id": post.get("id"),
        "title": post.get("title"),
        "author": post.get("author"),
        "selftext": post.get("selftext"),
        "permalink": post.get("permalink"),
    }
    if post.get("url") is not None:
        block["url"] = post.get("url")
    # Always emit search_results when present so the viewer can show an empty list vs missing.
    if "search_results" in post:
        block["search_results"] = post.get("search_results") or []
    return block


def _angle_embedding_for_frame(frame: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer the planned selection-channel angleEmbedding (selected-first ordering)."""
    plan = frame.get("embedding_plan")
    if not isinstance(plan, dict):
        return None
    angle_embedding = plan.get("angleEmbedding")
    return angle_embedding if isinstance(angle_embedding, dict) else None


def _comment_embedding_for_frame(
    frame: dict[str, Any], post: dict[str, Any], frame_bits: int
) -> dict[str, Any]:
    plan = frame.get("embedding_plan") if isinstance(frame.get("embedding_plan"), dict) else {}
    planned_comment = plan.get("commentEmbedding") if isinstance(plan, dict) else None
    comment: dict[str, Any] = {
        "bitsCount": frame_bits,
        "context": _context_block(post),
        "pickedCommentChain": _chain_bodies(post, frame.get("parent_id")),
    }
    if isinstance(planned_comment, dict):
        for key in ("bitsUsed", "bitsCount", "recoverableBitsCount", "insufficientBits"):
            if key in planned_comment:
                comment[key] = planned_comment[key]
        # Frame capacity is parent+tangent; keep that as the displayed embedded width when set.
        if frame_bits:
            comment["bitsCount"] = frame_bits
    return comment


def _frame_output_rows(
    frame: dict[str, Any], post: dict[str, Any], payload: str, frame_bits: int
) -> list[dict[str, Any]]:
    """Shape one frame like the single-frame output-results contract."""
    embedding: dict[str, Any] = {
        "compression": {"payload": payload},
        "commentEmbedding": _comment_embedding_for_frame(frame, post, frame_bits),
        "multiFrame": {
            "parentId": frame.get("parent_id"),
            "parentRecoverableWidth": frame.get("parent_recoverable_width"),
            "tangentRecoverableWidth": frame.get("tangent_recoverable_width"),
            "contextDictionaryId": frame.get("context_dictionary_id"),
            "tangentHash": frame.get("tangent_hash"),
            "selectedAngleIndex": frame.get("selected_angle_index"),
        },
        "senderAudit": frame.get("sender_audit"),
    }
    angle_embedding = _angle_embedding_for_frame(frame)
    if angle_embedding is not None:
        embedding["angleEmbedding"] = angle_embedding
    return [
        {
            "stegoText": frame.get("stego_text", ""),
            "post": _context_block(post),
            "embedding": embedding,
        }
    ]


def _frame_bit_width(frame: dict[str, Any]) -> int:
    parent = int(frame.get("parent_recoverable_width") or 0)
    tangent = int(frame.get("tangent_recoverable_width") or 0)
    return parent + tangent


def _write_frames(
    encoded: dict[str, Any],
    posts: list[dict[str, Any]],
    payload: str,
    output_dir: Path,
    sample_index: int,
) -> list[dict[str, Any]]:
    by_id = {str(post.get("id")): post for post in posts}
    entries: list[dict[str, Any]] = []
    for frame_index, frame in enumerate(encoded.get("frames", [])):
        post = by_id.get(str(frame.get("post_id")))
        if post is None:
            continue
        bits = _frame_bit_width(frame)
        rows = _frame_output_rows(frame, post, payload, bits)
        name = f"{frame.get('post_id')}_mf_{sample_index:04d}_{frame_index}.json"
        path = output_dir / name
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        entries.append(
            {
                "post_id": frame.get("post_id"),
                "sample_index": sample_index,
                "frame_index": frame_index,
                "payload_hash": stable_hash(payload),
                "output_file": str(path.resolve()),
                "parent_id": frame.get("parent_id"),
                "parent_recoverable_width": frame.get("parent_recoverable_width"),
                "tangent_recoverable_width": frame.get("tangent_recoverable_width"),
                "context_dictionary_id": frame.get("context_dictionary_id"),
                "tangent_hash": frame.get("tangent_hash"),
                "embedded_bits": bits,
            }
        )
    return entries


def _decode_check(
    receiver: ReceiverPipeline, encoded: dict[str, Any], payload: str
) -> dict[str, Any]:
    decoded = receiver.run_multi_frame(
        encoded["posts"],
        "sender",
        ordered_frame_refs=encoded["ordered_frame_refs"],
        payload_transform=encoded.get("payload_transform") or "plain",
    )
    return {
        "succeeded": bool(decoded.get("succeeded")),
        "payload_match": decoded.get("payload") == payload,
        "error": decoded.get("error"),
    }


def _run_sample(
    sender: StegoPipeline,
    receiver: ReceiverPipeline,
    batch: list[dict[str, Any]],
    args: argparse.Namespace,
    sample_index: int,
    output_dir: Path,
) -> dict[str, Any]:
    payload = _sample_payload(batch, sample_index, args)
    started = time.perf_counter()
    encoded = sender.encode_payload_frames(
        payload,
        batch,
        max_frames_per_post=args.max_frames_per_post,
        max_retries=args.max_retries,
    )
    record: dict[str, Any] = {
        "sample_index": sample_index,
        "post_ids": [post.get("id") for post in batch],
        "succeeded": bool(encoded.get("succeeded")),
        "error": encoded.get("error"),
        "frame_count": encoded.get("frame_count"),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "recovery_meta": encoded.get("recovery_meta"),
        "entries": [],
        "decode": None,
    }
    if not encoded.get("succeeded"):
        return record
    record["entries"] = _write_frames(encoded, batch, payload, output_dir, sample_index)
    if not args.skip_receiver_decode:
        record["decode"] = _decode_check(receiver, encoded, payload)
    return record


def _resolve_run_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw) if Path(raw).is_absolute() else _REPO_ROOT / raw
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return _REPO_ROOT / "metrics" / "e2e_runs" / f"multi_frame_batch_{stamp}"


def main() -> int:
    args = _parse_args()
    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    angles_dir = Path(args.angles_dir)
    if not angles_dir.is_absolute():
        angles_dir = _REPO_ROOT / angles_dir
    posts = _load_posts(angles_dir)
    per_sample = max(1, args.posts_per_sample)
    batches = [posts[i : i + per_sample] for i in range(0, len(posts), per_sample)]
    batches = [batch for batch in batches if batch][: args.samples]
    if not batches:
        _LOG.error("no_usable_posts", angles_dir=str(angles_dir))
        return 1

    run_dir = _resolve_run_dir(args.run_dir)
    output_dir = run_dir / "output-results"
    output_dir.mkdir(parents=True, exist_ok=True)
    sender, receiver = StegoPipeline(), ReceiverPipeline()
    records: list[dict[str, Any]] = []
    for sample_index, batch in enumerate(batches):
        _LOG.info("sample_start", sample_index=sample_index, post_ids=[p.get("id") for p in batch])
        try:
            record = _run_sample(sender, receiver, batch, args, sample_index, output_dir)
        except Exception as exc:
            _LOG.opt(exception=True).error("sample_failed", sample_index=sample_index)
            record = {"sample_index": sample_index, "succeeded": False, "error": str(exc)}
        records.append(record)
        _LOG.info(
            "sample_complete",
            sample_index=sample_index,
            succeeded=record.get("succeeded"),
            frame_count=record.get("frame_count"),
            error=record.get("error"),
            decode=record.get("decode"),
        )

    entries = [entry for record in records for entry in record.get("entries") or []]
    summary = {
        "run_dir": str(run_dir.resolve()),
        "angles_dir": str(angles_dir.resolve()),
        "sampler": "context_weighted_v2",
        "encode_path": "encode_payload_frames",
        "samples_requested": args.samples,
        "samples_succeeded": sum(1 for r in records if r.get("succeeded")),
        "samples_failed": sum(1 for r in records if not r.get("succeeded")),
        "frames_total": len(entries),
        "records": records,
        "entries": entries,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _LOG.info(
        "multi_frame_batch_complete",
        run_dir=str(run_dir),
        samples_succeeded=summary["samples_succeeded"],
        samples_failed=summary["samples_failed"],
        frames_total=summary["frames_total"],
    )
    return 0


if __name__ == "__main__":
    append_current_pid_to_log()
    raise SystemExit(main())
