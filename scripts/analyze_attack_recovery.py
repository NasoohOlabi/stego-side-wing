"""Analyze receiver-recorded attack recovery with post-cluster bootstrap intervals."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _interval(values: list[float], iterations: int = 10_000) -> dict[str, float] | None:
    if not values:
        return None
    rng = random.Random(1337)
    count = len(values)
    samples = sorted(statistics.fmean(values[rng.randrange(count)] for _ in range(count)) for _ in range(iterations))
    return {"lower": samples[int(iterations * 0.025)], "upper": samples[int(iterations * 0.975)]}


def analyze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.get("applicable") and row.get("decode_ok") is not None:
            key = (str(row["method"]), str(row["attack"]), str(row["severity"]))
            groups[key][str(row["post_id"])].append(int(bool(row["decode_ok"])))
    reports = []
    for (method, attack, severity), posts in sorted(groups.items()):
        values = [statistics.fmean(outcomes) for outcomes in posts.values()]
        reports.append({
            "method": method,
            "attack": attack,
            "severity": severity,
            "independent_posts": len(values),
            "recovery_rate": statistics.fmean(values),
            "cluster_bootstrap_95_ci": _interval(values),
        })
    return {"inference_unit": "post_id", "attacks": reports}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line]
    Path(args.output).write_text(json.dumps(analyze(rows), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
