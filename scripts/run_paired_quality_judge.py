"""Run resumable cached G-Eval or thread-grounded factuality judgments."""

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

PROMPTS = {
    "geval": ROOT / "config/evaluation_prompts/geval_v1.txt",
    "thread_grounded_factuality": ROOT
    / "config/evaluation_prompts/thread_grounded_factuality_v1.txt",
}
GEVAL_FIELDS = ("coherence", "relevance", "fluency", "factual_consistency", "overall")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _score(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5
        else None
    )


def _parse(raw: str, metric: str) -> dict[str, Any]:
    normalized = raw.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        normalized = normalized.split("\n", 1)[1].rsplit("\n", 1)[0].strip()
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    if metric == "geval":
        raw_scores = parsed.get("scores")
        scores = raw_scores if isinstance(raw_scores, dict) else {}
        return {
            "scores": {key: _score(scores.get(key)) for key in GEVAL_FIELDS},
            "rationale": parsed.get("rationale"),
        }
    return {
        "score": _score(parsed.get("score")),
        "claim_count": parsed.get("claim_count")
        if isinstance(parsed.get("claim_count"), int)
        else None,
        "supported_claim_count": parsed.get("supported_claim_count")
        if isinstance(parsed.get("supported_claim_count"), int)
        else None,
        "contradicted_claim_count": parsed.get("contradicted_claim_count")
        if isinstance(parsed.get("contradicted_claim_count"), int)
        else None,
        "rationale": parsed.get("rationale"),
    }


def _render_template(template: str, task: dict[str, Any]) -> str:
    """Substitute only supported task fields; preserve JSON braces in prompt text."""
    rendered = template
    for key in ("thread_context", "reference_text", "candidate"):
        rendered = rendered.replace("{" + key + "}", str(task.get(key) or ""))
    return rendered


def _task(
    row: dict[str, Any], metric: str, prompt_hash: str, provider: str, model: str
) -> dict[str, Any]:
    identity = {
        "metric": metric,
        "pair_id": row.get("pair_id"),
        "method": row.get("method"),
        "candidate": row.get("stegotext", ""),
        "reference_text": row.get("reference_text", ""),
        "thread_context": row.get("thread_context", ""),
        "prompt_hash": prompt_hash,
        "provider": provider,
        "model": model,
    }
    task_id = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return {**identity, "task_id": task_id, "post_id": row.get("post_id")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="comparison_dataset/paired_rows.jsonl")
    parser.add_argument("--output", required=True, help="append-only cached judgments JSONL")
    parser.add_argument("--metric", choices=sorted(PROMPTS), required=True)
    parser.add_argument("--prompt")
    parser.add_argument("--provider", default="lm_studio")
    parser.add_argument("--model", default="google/gemma-3-12b")
    parser.add_argument(
        "--limit", type=int, help="Process at most this many uncached rows (smoke testing)."
    )
    args = parser.parse_args()
    prompt_path = Path(args.prompt) if args.prompt else PROMPTS[args.metric]
    template = prompt_path.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    done = {str(item.get("task_id")) for item in _load_jsonl(output)}
    llm = LLMAdapter()
    processed = 0
    with output.open("a", encoding="utf-8") as stream:
        for row in _load_jsonl(Path(args.input)):
            task = _task(row, args.metric, prompt_hash, args.provider, args.model)
            if task["task_id"] in done:
                continue
            if args.limit is not None and processed >= args.limit:
                break
            try:
                raw = llm.call_llm(
                    _render_template(template, task),
                    provider=args.provider,
                    model=args.model,
                    temperature=0.0,
                )
                parsed = _parse(raw, args.metric)
                error = None
            except Exception as exc:  # Preserve failure as a cacheable audit record.
                raw, parsed, error = "", _parse("{}", args.metric), str(exc)
            stream.write(
                json.dumps(
                    {
                        **task,
                        **parsed,
                        "raw_response": raw,
                        "error": error,
                        "temperature": 0.0,
                        "judge_prompt_sha256": prompt_hash,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()
            processed += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
