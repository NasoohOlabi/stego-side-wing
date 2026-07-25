"""Score cached M3/M4 judgments with post-clustered paired inference."""

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


def _bootstrap_ci(values: list[float], iterations: int = 10_000) -> dict[str, float] | None:
    if not values:
        return None
    rng = random.Random(int(hashlib.sha256(json.dumps(values).encode()).hexdigest()[:8], 16))
    size = len(values)
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
    metrics = {str(row.get("metric")) for row in rows}
    if len(metrics) != 1:
        raise ValueError("input must contain exactly one metric")
    valid = [row for row in rows if isinstance(row.get("score"), int) and 1 <= row["score"] <= 5]
    clusters: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in valid:
        clusters[str(row.get("post_id"))][str(row.get("method"))].append(float(row["score"]))
    differences = []
    for methods in clusters.values():
        if {"our_method", "zlg"}.issubset(methods):
            differences.append(
                statistics.fmean(methods["our_method"]) - statistics.fmean(methods["zlg"])
            )
    wins = sum(value > 0 for value in differences)
    losses = sum(value < 0 for value in differences)
    provenance = sorted(
        {
            (str(r.get("provider")), str(r.get("judge_model")), str(r.get("judge_prompt_sha256")))
            for r in valid
        }
    )
    method_scores = {
        method: statistics.fmean(
            float(row["score"]) for row in valid if row.get("method") == method
        )
        for method in ("our_method", "zlg")
        if any(row.get("method") == method for row in valid)
    }
    return {
        "metric": metrics.pop(),
        "scale": {"minimum": 1, "maximum": 5, "higher_is_better": True},
        "primary_inference_unit": "unique_post_id",
        "valid_judgments": len(valid),
        "invalid_judgments": len(rows) - len(valid),
        "independent_paired_post_clusters": len(differences),
        "method_row_level_means": method_scores,
        "our_minus_zlg_post_cluster_mean": statistics.fmean(differences) if differences else None,
        "our_minus_zlg_bootstrap_95_ci": _bootstrap_ci(differences),
        "post_cluster_wins": wins,
        "post_cluster_losses": losses,
        "post_cluster_ties": len(differences) - wins - losses,
        "two_sided_sign_test_p": _sign_test(wins, losses),
        "provenance": [
            {"provider": p, "judge_model": m, "judge_prompt_sha256": h} for p, m, h in provenance
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
        summary[result["metric"]] = result
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
