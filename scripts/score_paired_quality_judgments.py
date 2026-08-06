"""Merge cached quality judgments into paired rows and comparison summary."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

GEVAL_FIELDS = ("coherence", "relevance", "fluency", "factual_consistency", "overall")


def _load(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _summarize(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    valid = [
        row for row in rows if isinstance(row.get(key), (int, float)) and 1 <= float(row[key]) <= 5
    ]
    means = {
        method: _mean([float(row[key]) for row in valid if row.get("method") == method])
        for method in ("our_method", "zlg")
    }
    clusters: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in valid:
        clusters[str(row.get("post_id"))][str(row.get("method"))].append(float(row[key]))
    post_cluster_means = {
        method: _mean(
            [
                statistics.fmean(methods[method])
                for methods in clusters.values()
                if methods.get(method)
            ]
        )
        for method in ("our_method", "zlg")
    }
    deltas = [
        statistics.fmean(methods["our_method"]) - statistics.fmean(methods["zlg"])
        for methods in clusters.values()
        if {"our_method", "zlg"}.issubset(methods)
    ]
    wins, losses = sum(delta > 0 for delta in deltas), sum(delta < 0 for delta in deltas)
    trials = wins + losses
    p_value = (
        None
        if not trials
        else min(
            1.0, 2.0 * sum(math.comb(trials, i) for i in range(min(wins, losses) + 1)) / (2**trials)
        )
    )
    return {
        "scale": {"minimum": 1, "maximum": 5, "higher_is_better": True},
        "valid_judgments": len(valid),
        "method_row_level_means": means,
        "method_post_cluster_means": post_cluster_means,
        "independent_paired_post_clusters": len(deltas),
        "our_minus_zlg_post_cluster_mean": _mean(deltas),
        "post_cluster_wins": wins,
        "post_cluster_losses": losses,
        "post_cluster_ties": len(deltas) - wins - losses,
        "two_sided_sign_test_p": p_value,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, help="cached JSONL from run_paired_quality_judge.py"
    )
    parser.add_argument("--paired-rows", required=True)
    parser.add_argument("--comparison-summary", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    judgments = _load(Path(args.input))
    paired_path = Path(args.paired_rows)
    rows = _load(paired_path)
    by_key = {(str(row.get("pair_id")), str(row.get("method"))): row for row in judgments}
    for row in rows:
        judgment = by_key.get((str(row.get("pair_id")), str(row.get("method"))))
        if judgment is None:
            continue
        metric = judgment.get("metric")
        provenance = {
            "provider": judgment.get("provider"),
            "judge_model": judgment.get("model"),
            "judge_prompt_sha256": judgment.get("judge_prompt_sha256"),
        }
        row.setdefault("quality_metric_provenance", {})[str(metric)] = provenance
        if metric == "geval":
            raw_scores = judgment.get("scores")
            scores = raw_scores if isinstance(raw_scores, dict) else {}
            for field in GEVAL_FIELDS:
                row[f"geval_{field}"] = scores.get(field)
        elif metric == "thread_grounded_factuality":
            row["thread_grounded_factuality"] = judgment.get("score")
            row["thread_grounded_factuality_claim_count"] = judgment.get("claim_count")
            row["thread_grounded_factuality_supported_claim_count"] = judgment.get(
                "supported_claim_count"
            )
            row["thread_grounded_factuality_contradicted_claim_count"] = judgment.get(
                "contradicted_claim_count"
            )
    paired_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    summary_path = Path(args.comparison_summary)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    quality_summary = {"thread_grounded_factuality": _summarize(rows, "thread_grounded_factuality")}
    quality_summary["geval"] = {field: _summarize(rows, f"geval_{field}") for field in GEVAL_FIELDS}
    summary["quality_judgments"] = quality_summary
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    rendered = json.dumps(quality_summary, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
