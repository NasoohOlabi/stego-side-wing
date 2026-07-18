"""Generate low/medium/high paraphrases using an externally frozen prompt template."""

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


def _text(raw: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    return str(value.get("text") or "").strip() if isinstance(value, dict) else ""


def _append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _done(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {(str(row["post_id"]), str(row["method"]), int(row["carrier_index"])) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", default="lm_studio")
    parser.add_argument("--model", default="google/gemma-3-12b")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line]
    prompt_path, output = Path(args.prompt), Path(args.output)
    template, prompt_hash = prompt_path.read_text(encoding="utf-8"), hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    done, llm = _done(output), LLMAdapter()
    for row in rows:
        if not row.get("accepted"):
            continue
        for index, source in enumerate(row.get("stegotexts", [])):
            key = (str(row["post_id"]), str(row["method"]), index)
            if key in done:
                continue
            paraphrases, raw_responses = {}, {}
            for severity in ("low", "medium", "high"):
                prompt = template.format(severity=severity, text=str(source))
                raw = llm.call_llm(prompt, provider=args.provider, model=args.model, temperature=0.0)
                paraphrases[severity], raw_responses[severity] = _text(raw), raw
            _append(output, {
                "post_id": key[0], "method": key[1], "carrier_index": index,
                "paraphrases": paraphrases, "raw_responses": raw_responses,
                "model": args.model, "provider": args.provider, "temperature": 0.0,
                "prompt_sha256": prompt_hash,
            })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
