"""Evaluate a grouped character-ngram passive stego detector without row leakage."""

from __future__ import annotations

import argparse
import json
import math
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
    positive, negative = model
    vocab = set(positive) | set(negative)
    pos_total = sum(positive.values()) + len(vocab)
    neg_total = sum(negative.values()) + len(vocab)
    return sum(
        count
        * (
            math.log((positive[token] + 1) / max(1, pos_total))
            - math.log((negative[token] + 1) / max(1, neg_total))
        )
        for token, count in _ngrams(text).items()
    )


def _auc(labels: list[int], scores: list[float]) -> float | None:
    positives = [score for label, score in zip(labels, scores, strict=True) if label == 1]
    negatives = [score for label, score in zip(labels, scores, strict=True) if label == 0]
    if not positives or not negatives:
        return None
    wins = sum(p > n for p in positives for n in negatives)
    ties = sum(p == n for p in positives for n in negatives)
    return (wins + 0.5 * ties) / (len(positives) * len(negatives))


def analyze(rows: list[dict[str, Any]], method: str, folds: int = 5) -> dict[str, Any]:
    examples = _examples(rows, method)
    post_ids = sorted({post_id for post_id, _, _ in examples})
    fold_by_post = {post_id: index % folds for index, post_id in enumerate(post_ids)}
    labels: list[int] = []
    scores: list[float] = []
    for fold in range(folds):
        train = [row for row in examples if fold_by_post[row[0]] != fold]
        test = [row for row in examples if fold_by_post[row[0]] == fold]
        model = _model(train)
        labels.extend(label for _, label, _ in test)
        scores.extend(_score(text, model) for _, _, text in test)
    return {
        "method": method,
        "roc_auc": _auc(labels, scores),
        "examples": len(labels),
        "independent_post_clusters": len(post_ids),
        "folds": folds,
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
