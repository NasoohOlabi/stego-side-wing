"""Evaluate a grouped character-ngram passive stego detector without row leakage."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def _ngrams(text: str) -> Counter[str]:
    normalized = " ".join(text.lower().split())
    return Counter(normalized[index : index + 4] for index in range(max(0, len(normalized) - 3)))


def _examples(rows: list[dict[str, Any]], method: str) -> list[tuple[str, int, str]]:
    examples: list[tuple[str, int, str]] = []
    for row in rows:
        if row.get("method") != method or not row.get("accepted"):
            continue
        post_id = str(row.get("post_id"))
        stego = [str(text) for text in row.get("stegotexts", []) if str(text).strip()]
        human = [str(text) for text in row.get("human_texts", []) if str(text).strip()]
        examples.extend((post_id, 1, text) for text in stego)
        examples.extend((post_id, 0, text) for text in human[: max(1, len(stego))])
    return examples


def _model(examples: list[tuple[str, int, str]]) -> tuple[Counter[str], Counter[str]]:
    positive: Counter[str] = Counter()
    negative: Counter[str] = Counter()
    for _, label, text in examples:
        (positive if label else negative).update(_ngrams(text))
    return positive, negative


def _score(text: str, model: tuple[Counter[str], Counter[str]]) -> float:
    """Mean per-n-gram log-likelihood ratio.

    The summed ratio scales with text length, so an unnormalized score lets a length
    difference between the stego and human arms masquerade as detectability. Dividing by
    the n-gram count makes the statistic length-invariant.
    """
    positive, negative = model
    vocab = set(positive) | set(negative)
    pos_total = sum(positive.values()) + len(vocab)
    neg_total = sum(negative.values()) + len(vocab)
    ngrams = _ngrams(text)
    length = sum(ngrams.values())
    if length == 0:
        return 0.0
    return (
        sum(
            count
            * (
                math.log((positive[token] + 1) / max(1, pos_total))
                - math.log((negative[token] + 1) / max(1, neg_total))
            )
            for token, count in ngrams.items()
        )
        / length
    )


def _auc(labels: list[int], scores: list[float]) -> float | None:
    positives = [score for label, score in zip(labels, scores, strict=True) if label == 1]
    negatives = [score for label, score in zip(labels, scores, strict=True) if label == 0]
    if not positives or not negatives:
        return None
    wins = sum(p > n for p in positives for n in negatives)
    ties = sum(p == n for p in positives for n in negatives)
    return (wins + 0.5 * ties) / (len(positives) * len(negatives))


def _mean_length(examples: list[tuple[str, int, str]], label: int) -> float | None:
    lengths = [
        len(" ".join(text.lower().split()))
        for _, row_label, text in examples
        if row_label == label
    ]
    return statistics.fmean(lengths) if lengths else None


def analyze(rows: list[dict[str, Any]], method: str, folds: int = 5) -> dict[str, Any]:
    examples = _examples(rows, method)
    post_ids = sorted({post_id for post_id, _, _ in examples})
    fold_by_post = {post_id: index % folds for index, post_id in enumerate(post_ids)}
    fold_aucs: list[float] = []
    scored = 0
    for fold in range(folds):
        train = [row for row in examples if fold_by_post[row[0]] != fold]
        test = [row for row in examples if fold_by_post[row[0]] == fold]
        if not train or not test:
            continue
        model = _model(train)
        auc = _auc([label for _, label, _ in test], [_score(text, model) for _, _, text in test])
        scored += len(test)
        if auc is not None:
            fold_aucs.append(auc)
    return {
        "method": method,
        # Each fold trains its own model, so scores across folds share no common scale and
        # must not be pooled into a single ranking. AUC is computed per fold, then averaged.
        "roc_auc": statistics.fmean(fold_aucs) if fold_aucs else None,
        "roc_auc_per_fold": fold_aucs,
        "roc_auc_stdev": statistics.pstdev(fold_aucs) if len(fold_aucs) > 1 else None,
        "examples": scored,
        "independent_post_clusters": len(post_ids),
        "folds": folds,
        # Residual length imbalance between arms is the main confounder for this detector.
        "mean_stego_chars": _mean_length(examples, 1),
        "mean_human_chars": _mean_length(examples, 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in Path(args.input).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = {
        "detector": "character_4gram_multinomial_nb",
        "grouping": "post_id",
        "methods": [analyze(rows, method) for method in ("our_method", "official_zgls")],
    }
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
