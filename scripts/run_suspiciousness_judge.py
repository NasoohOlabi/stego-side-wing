"""Execute blinded suspiciousness tasks using an externally frozen judge prompt."""

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


def _selected_index(raw: str) -> int | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    selected = value.get("suspicious_index") if isinstance(value, dict) else None
    return selected if isinstance(selected, int) and 0 <= selected <= 2 else None


def _done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        str(json.loads(line)["task_id"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    }


def _append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", default="lm_studio")
    parser.add_argument("--model", default="google/gemma-3-12b")
    args = parser.parse_args()
    tasks = [
        json.loads(line)
        for line in Path(args.tasks).read_text(encoding="utf-8").splitlines()
        if line
    ]
    prompt_path, output = Path(args.prompt), Path(args.output)
    template = prompt_path.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    done, llm = _done(output), LLMAdapter()
    for task in tasks:
        if str(task["task_id"]) in done:
            continue
        prompt = template.format(candidates_json=json.dumps(task["candidates"], ensure_ascii=False))
        raw = llm.call_llm(prompt, provider=args.provider, model=args.model, temperature=0.0)
        _append(
            output,
            {
                "task_id": task["task_id"],
                "selected_index": _selected_index(raw),
                "raw_response": raw,
                "judge_model": args.model,
                "provider": args.provider,
                "temperature": 0.0,
                "judge_prompt_sha256": prompt_hash,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
