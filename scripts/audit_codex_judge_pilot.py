"""Fail closed unless a Codex-judge pilot has sufficient valid, non-degenerate coverage."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

METRICS = ("standout", "weak_link", "suspicion", "attribution", "register")


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _metric_audit(directory: Path, metric: str) -> dict[str, Any]:
    rows = _load(directory / f"{metric}_judgments.jsonl")
    valid = [
        row for row in rows if row.get("error") is None and isinstance(row.get("result"), dict)
    ]
    answers = [row.get("answer") for row in valid if isinstance(row.get("answer"), dict)]
    index_key = {
        "standout": "inserted_index",
        "weak_link": "weakest_index",
        "attribution": "thread_index",
    }.get(metric)
    predicted = Counter(row["result"].get(index_key) for row in valid) if index_key else Counter()
    true = Counter(answer.get(index_key) for answer in answers) if index_key else Counter()
    coverage = len(valid) / len(rows) if rows else 0.0
    dominant = max(predicted.values(), default=0) / len(valid) if valid else 1.0
    return {
        "tasks": len(rows),
        "valid": len(valid),
        "coverage": coverage,
        "errors": len(rows) - len(valid),
        "predicted_index_counts": dict(predicted),
        "true_index_counts": dict(true),
        "dominant_prediction_share": dominant,
        "healthy": coverage >= 0.95 and dominant < 0.8,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    directory = Path(args.run_dir) / "comparison_dataset" / "codex_judgments"
    report = {metric: _metric_audit(directory, metric) for metric in METRICS}
    healthy = all(item["healthy"] for item in report.values())
    (directory / "pilot_audit.json").write_text(
        json.dumps({"healthy": healthy, "metrics": report}, indent=2), encoding="utf-8"
    )
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
