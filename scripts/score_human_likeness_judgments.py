"""Summarize cached M1 judgments and optionally ingest them into summary.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _score(row: dict[str, Any]) -> float | None:
    winner = row.get("winning_method")
    if winner == "our_method":
        return 1.0
    if winner == "zlg":
        return 0.0
    return 0.5 if row.get("winner") == "tie" else None


def _bootstrap_ci(values: list[float], iterations: int = 10_000) -> dict[str, float] | None:
    if not values:
        return None
    seed = int(hashlib.sha256(json.dumps(values).encode()).hexdigest()[:8], 16)
    rng, size = random.Random(seed), len(values)
    means = sorted(
        statistics.fmean(values[rng.randrange(size)] for _ in range(size))
        for _ in range(iterations)
    )
    return {"lower": means[int(0.025 * iterations)], "upper": means[int(0.975 * iterations)]}


def _sign_test(wins: int, losses: int) -> float | None:
    trials = wins + losses
    if not trials:
        return None
    tail = sum(math.comb(trials, i) for i in range(min(wins, losses) + 1))
    return min(1.0, 2.0 * tail / (2**trials))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [(row, score) for row in rows if (score := _score(row)) is not None]
    clusters: dict[str, list[float]] = defaultdict(list)
    for row, score in valid:
        clusters[str(row.get("post_id"))].append(score)
    post_scores = [statistics.fmean(scores) for _, scores in sorted(clusters.items())]
    wins = sum(score > 0.5 for score in post_scores)
    losses = sum(score < 0.5 for score in post_scores)
    provenance = sorted(
        {
            (
                str(row.get("provider")),
                str(row.get("judge_model")),
                str(row.get("judge_prompt_sha256")),
            )
            for row, _ in valid
        }
    )
    return {
        "metric": "m1_pairwise_human_likeness",
        "primary_inference_unit": "unique_post_id",
        "valid_judgments": len(valid),
        "invalid_judgments": len(rows) - len(valid),
        "independent_post_clusters": len(post_scores),
        "our_method_score": statistics.fmean(post_scores) if post_scores else None,
        "our_method_score_bootstrap_95_ci": _bootstrap_ci(post_scores),
        "post_cluster_wins": wins,
        "post_cluster_losses": losses,
        "post_cluster_ties": len(post_scores) - wins - losses,
        "two_sided_sign_test_p": _sign_test(wins, losses),
        "row_level_our_method_score": statistics.fmean(score for _, score in valid)
        if valid
        else None,
        "provenance": [
            {"provider": provider, "judge_model": model, "judge_prompt_sha256": prompt_hash}
            for provider, model, prompt_hash in provenance
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    parser.add_argument("--comparison-summary")
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line
    ]
    result = summarize(rows)
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.comparison_summary:
        path = Path(args.comparison_summary)
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary["human_likeness_preference"] = result
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
