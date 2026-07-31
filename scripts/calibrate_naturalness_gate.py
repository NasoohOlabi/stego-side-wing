"""Report what the shared naturalness gate does to human text and to each method.

The deployed ZLG gate was never calibrated against real writing, so nobody
noticed it rejected 23.3% of human Reddit comments -- and that rejection rate
was being reported as the baseline's failure rate. Run this after any threshold
change:

    uv run python scripts/calibrate_naturalness_gate.py \
        --zlg-run-dir metrics/zlg_comparison_runs/zlg_batch_scale300

Human rejection above ~5% means the gate is measuring style, not defects.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loguru import logger  # noqa: E402

from services.naturalness_gate_service import (  # noqa: E402
    NaturalnessThresholds,
    evaluate_naturalness,
)

MIN_CALIBRATION_WORDS = 8
HUMAN_REJECTION_BUDGET = 0.05

log = logger.bind(component="calibrate_naturalness_gate")


def _load_rows(results_jsonl: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with results_jsonl.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    rows.append(parsed)
    return rows


def _human_corpus(rows: list[dict[str, Any]]) -> list[str]:
    """Cover sentences are real comments from the source threads."""
    seen = {
        text.strip()
        for row in rows
        for text in (row.get("cover_texts") or [])
        if isinstance(text, str)
    }
    return sorted(t for t in seen if len(t.split()) >= MIN_CALIBRATION_WORDS)


def _accepted_stegotexts(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(row["stegotext"])
        for row in rows
        if row.get("accepted") and isinstance(row.get("stegotext"), str)
    ]


def _report(label: str, texts: list[str], thresholds: NaturalnessThresholds) -> dict[str, Any]:
    outcomes = [evaluate_naturalness(text, thresholds) for text in texts]
    rejected = [o for o in outcomes if not o.passed]
    by_rule = Counter(rule for outcome in rejected for rule in outcome.failed_rules)
    summary = {
        "corpus": label,
        "n": len(texts),
        "rejected": len(rejected),
        "rejection_rate": (len(rejected) / len(texts)) if texts else 0.0,
        "failed_rules": dict(by_rule.most_common()),
    }
    log.info("gate_calibration_corpus", **summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zlg-run-dir", required=True)
    parser.add_argument("--max-bigram-repeat-limit", type=int, default=None)
    parser.add_argument("--max-words", type=int, default=None)
    args = parser.parse_args()

    overrides = {
        key: value
        for key, value in (
            ("max_bigram_repeat_limit", args.max_bigram_repeat_limit),
            ("max_words", args.max_words),
        )
        if value is not None
    }
    thresholds = NaturalnessThresholds(**overrides)
    rows = _load_rows(Path(args.zlg_run_dir).resolve() / "results.jsonl")

    human = _report("human_cover_sentences", _human_corpus(rows), thresholds)
    zlg = _report("zlg_accepted_stegotext", _accepted_stegotexts(rows), thresholds)
    within_budget = human["rejection_rate"] <= HUMAN_REJECTION_BUDGET
    log.info(
        "gate_calibration_summary",
        thresholds=thresholds.model_dump(),
        human_rejection_rate=human["rejection_rate"],
        human_rejection_budget=HUMAN_REJECTION_BUDGET,
        within_budget=within_budget,
    )
    sys.stdout.write(
        json.dumps(
            {
                "thresholds": thresholds.model_dump(),
                "corpora": [human, zlg],
                "within_human_budget": within_budget,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0 if within_budget else 1


if __name__ == "__main__":
    raise SystemExit(main())
