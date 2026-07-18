"""Run the cached, blinded M1 pairwise human-likeness judge."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from workflows.adapters.llm import LLMAdapter  # noqa: E402


def build_tasks(rows: list[dict[str, Any]], prompt_hash: str, model: str) -> list[dict[str, Any]]:
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        pairs.setdefault(str(row["pair_id"]), {})[str(row["method"])] = row
    tasks = []
    for pair_id, pair in sorted(pairs.items()):
        if not {"our_method", "zlg"}.issubset(pair):
            continue
        ours, zlg = pair["our_method"], pair["zlg"]
        seed = int(hashlib.sha256(f"m1:{pair_id}".encode()).hexdigest()[:8], 16)
        methods = ["our_method", "zlg"]
        random.Random(seed).shuffle(methods)
        by_method = {"our_method": ours, "zlg": zlg}
        tasks.append(
            {
                "task_id": hashlib.sha256(
                    f"m1:{pair_id}:{prompt_hash}:{model}".encode()
                ).hexdigest(),
                "pair_id": pair_id,
                "post_id": ours.get("post_id"),
                "thread_context": ours.get("thread_context") or ours.get("post_text") or "",
                "candidate_a": by_method[methods[0]].get("stegotext", ""),
                "candidate_b": by_method[methods[1]].get("stegotext", ""),
                "method_a": methods[0],
                "method_b": methods[1],
                "order_seed": seed,
            }
        )
    return tasks


def parse_result(raw: str, task: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    winner = parsed.get("winner") if isinstance(parsed, dict) else None
    method = task.get(f"method_{str(winner).lower()}") if winner in {"A", "B"} else None
    return {
        "winner": winner if winner in {"A", "B", "tie"} else None,
        "winning_method": method,
        "rationale": parsed.get("rationale") if isinstance(parsed, dict) else None,
    }


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--prompt", default=str(ROOT / "config/evaluation_prompts/human_likeness_pairwise_v1.txt")
    )
    parser.add_argument("--provider", default="lm_studio")
    parser.add_argument("--model", default="google/gemma-3-12b")
    args = parser.parse_args()
    prompt_path, output = Path(args.prompt), Path(args.output)
    template = prompt_path.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    done = {str(row["task_id"]) for row in _load(output)} if output.exists() else set()
    llm = LLMAdapter()
    with output.open("a", encoding="utf-8") as stream:
        for task in build_tasks(_load(Path(args.input)), prompt_hash, args.model):
            if task["task_id"] in done:
                continue
            raw = llm.call_llm(
                template.format(**task), provider=args.provider, model=args.model, temperature=0.0
            )
            result = {
                **task,
                **parse_result(raw, task),
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
