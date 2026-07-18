"""Run method-specific receivers against an attacked carrier corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.zlg_comparison_service import (  # noqa: E402
    _post_json,
)
from workflows.pipelines.receiver import ReceiverPipeline  # noqa: E402


def _replace_comment(comments: list[dict[str, Any]], comment_id: str, body: str) -> bool:
    for comment in comments:
        if str(comment.get("id")) == comment_id:
            comment["body"] = body
            return True
        replies = comment.get("replies")
        if isinstance(replies, list) and _replace_comment(replies, comment_id, body):
            return True
    return False


def _mutate_context(posts: list[dict[str, Any]], seed: int) -> None:
    candidates = [post for post in posts if isinstance(post.get("comments"), list) and post["comments"]]
    if not candidates:
        return
    comments = candidates[0]["comments"]
    comments.pop(seed % len(comments))


def _mutate_zlg_prompt(prompt: str, seed: int) -> str:
    lines = prompt.splitlines()
    candidates = [index for index, line in enumerate(lines) if line.startswith("- ")]
    if not candidates:
        return prompt
    lines.pop(candidates[seed % len(candidates)])
    return "\n".join(lines)


def _run_our(row: dict[str, Any], artifact: dict[str, Any]) -> bool:
    posts = json.loads(json.dumps(artifact["posts"]))
    refs = artifact["ordered_frame_refs"]
    if row["attack"] == "context_mutation":
        _mutate_context(posts, int(row["attack_seed"]))
    else:
        ref = refs[int(row["carrier_index"])]
        target = next(post for post in posts if str(post.get("id")) == str(ref["post_id"]))
        if not _replace_comment(target.get("comments", []), str(ref["comment_id"]), str(row["text"])):
            raise RuntimeError("Sender frame comment was not found")
    decoded = ReceiverPipeline().run_multi_frame(
        posts,
        "sender",
        ordered_frame_refs=refs,
        payload_transform=str(artifact.get("payload_transform") or "plain"),
    )
    return bool(decoded.get("succeeded") and decoded.get("payload") == artifact["target_payload"])


def _run_zlg(row: dict[str, Any], artifact: dict[str, Any]) -> bool:
    frame = artifact["frames"][int(row["carrier_index"])]
    context = dict(frame.get("reveal_context") or {})
    prompt = str(context.pop("prompt", ""))
    if row["attack"] == "context_mutation":
        prompt = _mutate_zlg_prompt(prompt, int(row["attack_seed"]))
    response = _post_json(
        f"{str(artifact['server_url']).rstrip('/')}/reveal",
        {
            "prompt": prompt,
            "stegotext": str(row["text"]),
            **context,
            "__timeout_seconds__": 3600,
        },
    )
    return bool(response.get("decode_ok") and response.get("secret") == frame["payload_segment"])


def evaluate(row: dict[str, Any]) -> dict[str, Any]:
    if not row.get("applicable"):
        return {**row, "decode_ok": None, "decode_reason": "not_applicable"}
    try:
        artifact = json.loads(Path(str(row["receiver_artifact"])).read_text(encoding="utf-8"))
        recovered = _run_our(row, artifact) if row["method"] == "our_method" else _run_zlg(row, artifact)
        return {**row, "decode_ok": recovered, "decode_reason": None}
    except Exception as exc:
        return {**row, "decode_ok": False, "decode_reason": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line]
    evaluated = [evaluate(row) for row in rows]
    Path(args.output).write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in evaluated) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
