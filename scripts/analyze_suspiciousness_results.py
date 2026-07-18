"""Analyze paired suspiciousness judgments using posts as independent clusters."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _exact_sign_p(positive: int, negative: int) -> float | None:
    n = positive + negative
    if n == 0:
        return None
    tail = sum(math.comb(n, k) for k in range(min(positive, negative) + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def analyze(rows: list[dict[str, Any]], *, iterations: int = 10_000) -> dict[str, Any]:
    by_post: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        method = str(row.get("method"))
        if method not in {"our_method", "zlg"} or row.get("valid") is not True:
            continue
        by_post[str(row.get("post_id"))][method].append(int(bool(row.get("correct"))))
    clusters = {
        post_id: methods
        for post_id, methods in by_post.items()
        if methods.get("our_method") and methods.get("zlg")
    }
    deltas = [
        statistics.fmean(methods["zlg"]) - statistics.fmean(methods["our_method"])
        for methods in clusters.values()
    ]
    rng = random.Random(1337)
    bootstrap: list[float] = []
    if deltas:
        n = len(deltas)
        bootstrap = sorted(
            statistics.fmean(deltas[rng.randrange(n)] for _ in range(n)) for _ in range(iterations)
        )
    positive = sum(delta > 0 for delta in deltas)
    negative = sum(delta < 0 for delta in deltas)
    return {
        "inference_unit": "post_id",
        "independent_post_clusters": len(deltas),
        "mean_detection_rate_delta_zlg_minus_our": statistics.fmean(deltas) if deltas else None,
        "cluster_bootstrap_95_ci": (
            {
                "lower": bootstrap[int(0.025 * iterations)],
                "upper": bootstrap[min(iterations - 1, int(0.975 * iterations))],
            }
            if bootstrap
            else None
        ),
        "post_level_direction_counts": {
            "zlg_higher": positive,
            "our_method_higher": negative,
            "ties": len(deltas) - positive - negative,
        },
        "post_level_exact_sign_p": _exact_sign_p(positive, negative),
        "valid_rows": sum(
            len(method_rows) for methods in clusters.values() for method_rows in methods.values()
        ),
        "caveat": (
            "Inference is clustered by post. Results still condition on accepted generations and "
            "require the judge prompt, decoy construction, and model provenance for reproduction."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    input_path = Path(args.input).resolve()
    rows = [
        value
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and isinstance((value := json.loads(line)), dict)
    ]
    report = analyze(rows)
    rendered = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        Path(args.output).resolve().write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
