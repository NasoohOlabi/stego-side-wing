"""Minimal local multi-frame stego benchmark harness."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from workflows.pipelines.receiver import ReceiverPipeline
from workflows.pipelines.stego import StegoPipeline


def _synthetic_posts() -> list[dict]:
    return [
        {
            "id": f"post{i}",
            "title": f"title {i}",
            "selftext": "",
            "comments": [{"id": f"c{i}", "author": "u", "body": f"body {i}", "replies": []}],
            "angles": [{"source_quote": f"q{i}", "tangent": f"t{i}", "category": f"cat{i}"}],
        }
        for i in range(1, 6)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["synthetic", "saved-posts"], default="synthetic")
    parser.add_argument("--post-file")
    parser.add_argument("--payload-bits", type=int, default=256)
    parser.add_argument("--max-frames-per-post", type=int, default=3)
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()

    posts = _synthetic_posts()
    if args.mode == "saved-posts" and args.post_file:
        posts = json.loads(Path(args.post_file).read_text(encoding="utf-8"))

    payload = "x" * max(1, args.payload_bits // 8)
    sender = StegoPipeline()
    receiver = ReceiverPipeline()
    encoded = sender.encode_payload_frames(
        payload,
        posts,
        max_frames_per_post=args.max_frames_per_post,
    )
    decoded = (
        receiver.run_multi_frame(encoded["posts"], "sender", payload_transform="plain")
        if encoded.get("succeeded")
        else {"succeeded": False}
    )
    out_dir = (
        Path("metrics")
        / "e2e_runs"
        / f"multi_frame_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(
        json.dumps({"encoded": encoded, "decoded": decoded}, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
