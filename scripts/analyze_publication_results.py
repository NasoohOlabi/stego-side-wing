"""Score paired publication attempts and report post-clustered comparisons."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.stego_metrics_service import (  # noqa: E402
    count_model_tokens,
    run_single_post_metrics,
)

METHODS = ("our_method", "official_zgls")


def _quality(metrics: dict[str, Any]) -> dict[str, Any]:
    matched = metrics.get("primary_baseline_matched_post") or {}
    return {
        "perplexity": metrics.get("perplexity"),
        "matched_post_kl": matched.get("kl_stego_vs_matched_post"),
        "matched_post_jsd": matched.get("jsd_stego_vs_matched_post"),
    }


def _score_text(
    post_id: str, method: str, index: int, text: str, temp: Path, dataset: Path, device: str
) -> dict[str, Any]:
    path = temp / f"{post_id}_version_{method}_{index}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"stegoText": text}], ensure_ascii=False), encoding="utf-8")
    return _quality(run_single_post_metrics(path, dataset, device=device))


def score_quality(
    rows: list[dict[str, Any]], temp: Path, dataset: Path, device: str
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("accepted"):
            continue
        for index, text in enumerate(row.get("stegotexts", [])):
            scored.append(
                {
                    "post_id": row["post_id"],
                    "method": row["method"],
                    "carrier_index": index,
                    **_score_text(
                        str(row["post_id"]),
                        str(row["method"]),
                        index,
                        str(text),
                        temp,
                        dataset,
                        device,
                    ),
                }
            )
    return scored


def _mean(values: list[float]) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return statistics.fmean(finite) if finite else None


def _row_capacity_bits(row: dict[str, Any]) -> int:
    """Selection-channel capacity offered by the carriers used in one attempt."""
    frames = row.get("frames") or []
    return sum(int(f.get("capacity") or 0) for f in frames if isinstance(f, dict))


def _capacity_summary(accepted: list[dict[str, Any]]) -> dict[str, Any]:
    """Pooled capacity metrics exactly as defined in the paper.

    The paper's BPW/BPT are ratios of totals; ``effective_recovered_bits_per_word`` is a
    mean of per-sample ratios (matching the ZGLS baseline's ``evaluate/bpw.py``). The two
    differ whenever sample lengths vary, so both are reported side by side.
    """
    total_bits = sum(float(row.get("payload_bits_encoded") or 0) for row in accepted)
    total_words = sum(int(row.get("word_count") or 0) for row in accepted)
    total_embedded = sum(float(row.get("total_embedded_bits") or 0) for row in accepted)
    total_capacity = sum(_row_capacity_bits(row) for row in accepted)
    texts = [str(text) for row in accepted for text in row.get("stegotexts", [])]
    total_tokens = count_model_tokens(texts)
    return {
        "bits_per_word_pooled": (total_bits / total_words) if total_words else None,
        "bits_per_token_pooled": (total_bits / total_tokens) if total_tokens else None,
        "embedding_rate_mean_bits": _mean(
            [float(row.get("payload_bits_encoded") or 0) for row in accepted]
        ),
        "utilization_rate_percent": (
            100.0 * total_embedded / total_capacity if total_capacity else None
        ),
        "total_payload_bits": total_bits,
        "total_words": total_words,
        "total_tokens": total_tokens,
        "total_selection_capacity_bits": total_capacity,
        "bits_per_token_model": "gpt2" if total_tokens is not None else None,
    }


def _method_summary(
    rows: list[dict[str, Any]], quality: list[dict[str, Any]], method: str
) -> dict[str, Any]:
    attempts = [row for row in rows if row.get("method") == method]
    accepted = [row for row in attempts if row.get("accepted")]
    qrows = [row for row in quality if row.get("method") == method]
    reasons = Counter(
        str(row.get("reason") or "unknown") for row in attempts if not row.get("accepted")
    )
    return {
        "attempted": len(attempts),
        "accepted": len(accepted),
        "failed": len(attempts) - len(accepted),
        "attempt_success_rate": len(accepted) / len(attempts) if attempts else 0.0,
        "exact_recovery_rate": sum(bool(row.get("decode_ok")) for row in attempts) / len(attempts)
        if attempts
        else 0.0,
        # Macro-average (mean of per-sample ratios), kept for ZGLS comparability.
        "effective_recovered_bits_per_word": _mean(
            [
                float(row.get("payload_bits_encoded") or 0)
                / max(1, int(row.get("word_count") or 0))
                for row in accepted
            ]
        ),
        "capacity_metrics": _capacity_summary(accepted),
        "latency_ms": _mean([float(row.get("latency_ms") or 0) for row in attempts]),
        "perplexity": _mean(
            [row["perplexity"] for row in qrows if isinstance(row.get("perplexity"), (int, float))]
        ),
        "matched_post_jsd": _mean(
            [
                row["matched_post_jsd"]
                for row in qrows
                if isinstance(row.get("matched_post_jsd"), (int, float))
            ]
        ),
        "failure_taxonomy": dict(reasons),
    }


def _post_metric(
    rows: list[dict[str, Any]], quality: list[dict[str, Any]], metric: str
) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    source = quality if metric in {"perplexity", "matched_post_jsd"} else rows
    for row in source:
        if metric == "effective_recovered_bits_per_word" and not row.get("accepted"):
            continue
        value: Any = row.get(metric)
        if metric == "effective_recovered_bits_per_word":
            value = float(row.get("payload_bits_encoded") or 0) / max(
                1, int(row.get("word_count") or 0)
            )
        if isinstance(value, (int, float)) and math.isfinite(value):
            values[(str(row["post_id"]), str(row["method"]))].append(float(value))
    return {key: statistics.fmean(group) for key, group in values.items()}


def _sign_p(deltas: list[float]) -> float | None:
    positive, negative = sum(value > 0 for value in deltas), sum(value < 0 for value in deltas)
    count = positive + negative
    if not count:
        return None
    tail = sum(math.comb(count, index) for index in range(min(positive, negative) + 1)) / 2**count
    return min(1.0, 2 * tail)


def _bootstrap(deltas: list[float], iterations: int = 10_000) -> dict[str, float] | None:
    if not deltas:
        return None
    rng, count = random.Random(1337), len(deltas)
    means = sorted(
        statistics.fmean(deltas[rng.randrange(count)] for _ in range(count))
        for _ in range(iterations)
    )
    return {"lower": means[int(0.025 * iterations)], "upper": means[int(0.975 * iterations)]}


def _paired(rows: list[dict[str, Any]], quality: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for metric in (
        "effective_recovered_bits_per_word",
        "latency_ms",
        "perplexity",
        "matched_post_jsd",
    ):
        values = _post_metric(rows, quality, metric)
        posts = sorted(
            {post for post, method in values if method == "our_method"}
            & {post for post, method in values if method == "official_zgls"}
        )
        deltas = [values[(post, "official_zgls")] - values[(post, "our_method")] for post in posts]
        report[metric] = {
            "paired_posts": len(deltas),
            "mean_delta_zlg_minus_our": statistics.fmean(deltas) if deltas else None,
            "cluster_bootstrap_95_ci": _bootstrap(deltas),
            "sign_test_p": _sign_p(deltas),
        }
    _apply_holm(report)
    return report


def _apply_holm(report: dict[str, Any]) -> None:
    ranked = sorted(
        (block["sign_test_p"], name)
        for name, block in report.items()
        if block["sign_test_p"] is not None
    )
    running = 0.0
    count = len(ranked)
    for rank, (p_value, name) in enumerate(ranked):
        running = max(running, min(1.0, float(p_value) * (count - rank)))
        report[name]["holm_adjusted_p"] = running


def build_report(rows: list[dict[str, Any]], quality: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "inference_unit": "post_id",
        "methods": {method: _method_summary(rows, quality, method) for method in METHODS},
        "paired_comparisons": _paired(rows, quality),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line
    ]
    output = Path(args.output).resolve()
    quality = score_quality(
        rows, output.parent / "metric_inputs", Path(args.dataset_dir).resolve(), args.device
    )
    report = build_report(rows, quality)
    output.write_text(
        json.dumps({**report, "quality_rows": quality}, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
