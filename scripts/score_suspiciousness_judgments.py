"""Join raw judge responses with the blinded answer key for clustered analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PARSER_VERSION = 1


def score(keys: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_task = {str(row["task_id"]): row for row in judgments}
    rows: list[dict[str, Any]] = []
    for key in keys:
        judgment = by_task.get(str(key["task_id"]), {})
        selected = judgment.get("selected_index")
        valid = isinstance(selected, int) and 0 <= selected <= 2
        rows.append({
            **key,
            "valid": valid,
            "correct": bool(valid and selected == key["correct_index"]),
            "selected_index": selected,
            "raw_response": judgment.get("raw_response"),
            "parser_version": PARSER_VERSION,
        })
    return rows


def _load(path: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answer-key", required=True)
    parser.add_argument("--judgments", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = score(_load(args.answer_key), _load(args.judgments))
    Path(args.output).write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
