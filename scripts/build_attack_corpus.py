"""Build deterministic attacked carriers; decoding is recorded by the method receiver."""

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

from services.benchmark_attack_service import attack_variants  # noqa: E402


def _synonym(word: str) -> str | None:
    try:
        from nltk.corpus import wordnet
    except ImportError as exc:
        raise RuntimeError("Install the metrics extra with NLTK for synonym attacks") from exc
    for synset in wordnet.synsets(word):
        for lemma in synset.lemmas():
            candidate = lemma.name().replace("_", " ")
            if candidate.lower() != word.lower() and " " not in candidate:
                return candidate
    return None


def _load_paraphrases(path: str) -> dict[tuple[str, str, int], dict[str, str]]:
    if not path:
        return {}
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    return {
        (str(row["post_id"]), str(row["method"]), int(row["carrier_index"])): dict(row["paraphrases"])
        for row in rows
    }


def build(rows: list[dict[str, Any]], paraphrases: dict[tuple[str, str, int], dict[str, str]]) -> list[dict[str, Any]]:
    attacked: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("accepted"):
            continue
        post_id, method = str(row["post_id"]), str(row["method"])
        context = [str(value) for value in row.get("human_texts", [])]
        for index, text in enumerate(row.get("stegotexts", [])):
            seed = int(hashlib.sha256(f"{post_id}:{method}:{index}".encode()).hexdigest()[:8], 16)
            variants = attack_variants(
                str(text), context, seed, _synonym, paraphrases.get((post_id, method, index))
            )
            attacked.extend(
                {
                    "post_id": post_id,
                    "method": method,
                    "carrier_index": index,
                    "attack_seed": seed,
                    "receiver_artifact": row.get("receiver_artifact"),
                    **variant,
                }
                for variant in variants
            )
    return attacked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--paraphrases", default="")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line]
    attacked = build(rows, _load_paraphrases(args.paraphrases))
    Path(args.output).write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in attacked) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
