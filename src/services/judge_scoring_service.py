"""Post-clustered statistics used by Codex judge reports."""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import validate_call


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


@validate_call
def post_cluster_summary(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    clusters: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            clusters[str(row.get("post_id"))][str(row.get("method"))].append(float(value))
    deltas = [
        statistics.fmean(x["our_method"]) - statistics.fmean(x["zlg"])
        for x in clusters.values()
        if x.get("our_method") and x.get("zlg")
    ]
    wins, losses = sum(x > 0 for x in deltas), sum(x < 0 for x in deltas)
    trials = wins + losses
    p = (
        min(1.0, 2 * sum(math.comb(trials, i) for i in range(min(wins, losses) + 1)) / 2**trials)
        if trials
        else None
    )
    return {
        "independent_paired_post_clusters": len(deltas),
        "our_minus_zlg_post_cluster_mean": _mean(deltas),
        "post_cluster_wins": wins,
        "post_cluster_losses": losses,
        "post_cluster_ties": len(deltas) - trials,
        "two_sided_sign_test_p": p,
    }


@validate_call
def mcnemar_exact(pairs: list[tuple[bool, bool]]) -> float | None:
    discordant = sum(a != b for a, b in pairs)
    if not discordant:
        return None
    lesser = min(sum(a and not b for a, b in pairs), sum(b and not a for a, b in pairs))
    return min(1.0, 2 * sum(math.comb(discordant, i) for i in range(lesser + 1)) / 2**discordant)


def cluster_bootstrap_ci(
    clusters: dict[str, Any], stat: Callable[[list[Any]], float], iterations: int = 10_000
) -> tuple[float | None, float | None]:
    values = list(clusters.values())
    if not values:
        return None, None
    rng = random.Random(0)
    estimates = sorted(stat([rng.choice(values) for _ in values]) for _ in range(iterations))
    return estimates[int(0.025 * (iterations - 1))], estimates[int(0.975 * (iterations - 1))]


@validate_call
def auroc(positive_scores: list[float], negative_scores: list[float]) -> float | None:
    if not positive_scores or not negative_scores:
        return None
    wins = sum(
        1 if p > n else 0.5 if p == n else 0 for p in positive_scores for n in negative_scores
    )
    return wins / (len(positive_scores) * len(negative_scores))


@validate_call
def holm_adjust(p_values: list[float | None]) -> list[float | None]:
    indexed = sorted((p, i) for i, p in enumerate(p_values) if p is not None)
    output: list[float | None] = [None] * len(p_values)
    prior = 0.0
    count = len(indexed)
    for rank, (p, index) in enumerate(indexed):
        prior = max(prior, min(1.0, p * (count - rank)))
        output[index] = prior
    return output
