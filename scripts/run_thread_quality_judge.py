"""Run cached M3 thread-relevance or M4 writing-quality judgments."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from workflows.adapters.llm import LLMAdapter  # noqa: E402

METRICS = {
    "thread_relevance": ROOT / "config/evaluation_prompts/thread_relevance_v1.txt",
    "writing_quality": ROOT / "config/evaluation_prompts/writing_quality_v1.txt",
}


def build_tasks(
    rows: list[dict[str, Any]], metric: str, prompt_hash: str, model: str
) -> list[dict[str, Any]]:
    tasks = []
    for row in rows:
        pair_id, method = str(row["pair_id"]), str(row["method"])
        tasks.append(
            {
                "task_id": hashlib.sha256(
                    f"{metric}:{pair_id}:{method}:{prompt_hash}:{model}".encode()
                ).hexdigest(),
                "metric": metric,
                "pair_id": pair_id,
                "post_id": row.get("post_id"),
                "method": method,
                "thread_context": row.get("thread_context") or row.get("post_text") or "",
                "candidate": row.get("stegotext", ""),
            }
        )
    return sorted(tasks, key=lambda task: (task["pair_id"], task["method"]))


def parse_result(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    score = parsed.get("score") if isinstance(parsed, dict) else None
    valid_score = (
        score
        if isinstance(score, int) and not isinstance(score, bool) and 1 <= score <= 5
        else None
    )
    return {
        "score": valid_score,
        "rationale": parsed.get("rationale") if isinstance(parsed, dict) else None,
    }


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metric", choices=sorted(METRICS), required=True)
    parser.add_argument("--prompt")
    parser.add_argument("--provider", default="lm_studio")
    parser.add_argument("--model", default="google/gemma-3-12b")
    args = parser.parse_args()
    prompt_path = Path(args.prompt) if args.prompt else METRICS[args.metric]
    template = prompt_path.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    output = Path(args.output)
    done = {str(row["task_id"]) for row in _load(output)} if output.exists() else set()
    llm = LLMAdapter()
    with output.open("a", encoding="utf-8") as stream:
        for task in build_tasks(_load(Path(args.input)), args.metric, prompt_hash, args.model):
            if task["task_id"] in done:
                continue
            raw = llm.call_llm(
                template.format(**task), provider=args.provider, model=args.model, temperature=0.0
            )
            result = {
                **task,
                **parse_result(raw),
                "raw_response": raw,
                "judge_model": args.model,
                "provider": args.provider,
                "temperature": 0.0,
                "judge_prompt_sha256": prompt_hash,
            }
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
