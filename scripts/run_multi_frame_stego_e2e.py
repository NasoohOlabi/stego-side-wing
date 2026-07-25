"""Minimal local multi-frame stego benchmark harness."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from workflows.pipelines.receiver import ReceiverPipeline
from workflows.pipelines.stego import StegoPipeline


def _synthetic_posts() -> list[dict]:
    threads = [
        (
            "What is one small habit that made working from home easier?",
            "I have been struggling to keep a routine while working remotely. I am looking for practical ideas that do not require buying new equipment.",
            [
                "I put my laptop away after work so the day has a clear ending.",
                "A short walk before starting helps me switch into work mode.",
                "Writing tomorrow's first task before I log off reduces morning friction.",
                "I keep a water bottle on the desk because small breaks add up.",
            ],
            [
                (
                    "A transition ritual can separate work time from home time.",
                    "Suggest a low-effort start or end-of-day ritual.",
                    "routine",
                ),
                (
                    "Environmental cues can support a habit without relying on willpower.",
                    "Discuss changing one small part of the workspace.",
                    "environment",
                ),
                (
                    "Planning the next action can make it easier to begin.",
                    "Offer a concrete way to prepare the next work session.",
                    "planning",
                ),
                (
                    "Brief movement breaks can improve focus during long sessions.",
                    "Share a simple movement or break idea.",
                    "wellbeing",
                ),
            ],
        ),
        (
            "How do you make weeknight cooking feel less exhausting?",
            "I want to cook more at home, but I am usually tired after work and end up ordering food. What actually helps on busy evenings?",
            [
                "I cook double portions and freeze a few individual servings.",
                "Keeping two very simple meals in rotation makes decisions easier.",
                "Chopping vegetables while listening to a podcast makes prep less tedious.",
                "I start rice or pasta first, then choose the rest while it cooks.",
            ],
            [
                (
                    "Reducing decisions can make a routine more sustainable.",
                    "Recommend a way to simplify meal choices.",
                    "planning",
                ),
                (
                    "Preparing extra portions can save effort later in the week.",
                    "Suggest a batch-cooking strategy.",
                    "meal prep",
                ),
                (
                    "Pairing a chore with something enjoyable can help it feel lighter.",
                    "Share a pleasant cooking companion or ritual.",
                    "motivation",
                ),
                (
                    "Starting with one reliable staple creates momentum.",
                    "Offer a quick first step for dinner preparation.",
                    "cooking",
                ),
            ],
        ),
    ]
    posts: list[dict] = []
    for post_number, (title, selftext, comments, angles) in enumerate(threads, start=1):
        posts.append(
            {
                "id": f"synthetic-post-{post_number}",
                "title": title,
                "selftext": selftext,
                "comments": [
                    {
                        "id": f"synthetic-comment-{post_number}-{comment_number}",
                        "author": f"community_member_{comment_number}",
                        "body": body,
                        "replies": [],
                    }
                    for comment_number, body in enumerate(comments, start=1)
                ],
                "angles": [
                    {"source_quote": quote, "tangent": tangent, "category": category}
                    for quote, tangent, category in angles
                ],
            }
        )
    return posts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["synthetic", "saved-posts"], default="synthetic")
    parser.add_argument("--post-file")
    parser.add_argument("--payload-bits", type=int, default=256)
    parser.add_argument("--max-frames-per-post", type=int, default=3)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Maximum validated sender-generation retries per frame.",
    )
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
        max_retries=args.max_retries,
    )
    decoded = (
        receiver.run_multi_frame(
            encoded["posts"],
            "sender",
            ordered_frame_refs=encoded["ordered_frame_refs"],
            payload_transform="plain",
        )
        if encoded.get("succeeded")
        else {"succeeded": False}
    )
    out_dir = (
        Path("metrics") / "e2e_runs" / f"multi_frame_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(
        json.dumps({"encoded": encoded, "decoded": decoded}, indent=2),
        encoding="utf-8",
    )
    if not (
        encoded.get("succeeded") and decoded.get("succeeded") and decoded.get("payload") == payload
    ):
        raise SystemExit(f"Multi-frame E2E failed; inspect {out_dir / 'result.json'}")


if __name__ == "__main__":
    main()
