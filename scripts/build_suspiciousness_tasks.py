"""Create blinded, position-rotated suspiciousness tasks and a separate answer key."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


def build(rows: list[dict[str, Any]], prompt_hash: str, judge_model: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tasks: list[dict[str, Any]] = []
    keys: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("accepted"):
            continue
        humans = [str(value) for value in row.get("human_texts", []) if str(value).strip()]
        for carrier_index, stego in enumerate(row.get("stegotexts", [])):
            if len(humans) < 2:
                continue
            identity = f"{row['post_id']}:{row['method']}:{carrier_index}"
            seed = int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16)
            decoys = random.Random(seed).sample(humans, 2)
            candidates = [str(stego), *decoys]
            random.Random(seed + 1).shuffle(candidates)
            task_id = hashlib.sha256(f"judge:{identity}".encode()).hexdigest()
            tasks.append({
                "task_id": task_id,
                "post_id": row["post_id"],
                "candidates": candidates,
                "order_seed": seed + 1,
                "judge_model": judge_model,
                "judge_prompt_sha256": prompt_hash,
            })
            keys.append({
                "task_id": task_id,
                "post_id": row["post_id"],
                "method": row["method"],
                "carrier_index": carrier_index,
                "correct_index": candidates.index(str(stego)),
            })
    return tasks, keys


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--answer-key", required=True)
    parser.add_argument("--judge-prompt", required=True)
    parser.add_argument("--judge-model", default="google/gemma-3-12b")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line]
    prompt_hash = hashlib.sha256(Path(args.judge_prompt).read_bytes()).hexdigest()
    tasks, keys = build(rows, prompt_hash, args.judge_model)
    _write_jsonl(Path(args.tasks), tasks)
    _write_jsonl(Path(args.answer_key), keys)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
