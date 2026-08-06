"""Recompute the audit statistics for a paired ZLG comparison run.

Read-only: every input is opened for reading and the only file written is ``--output``.
The report backs ``docs/reports/zlg-sample-audit-2026-07-27.md`` and
``docs/reports/zlg-baseline-weaknesses-2026-07-27.md`` so those numbers can be
regenerated instead of hand-transcribed.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

Row = dict[str, Any]

OPENER_PHRASES = (
    "why people keep",
    "keep coming back",
    "keep bringing up",
    "keep circling back",
    "honestly,",
    "it feels like",
)

SURFACE_TESTS: dict[str, Callable[[str], bool]] = {
    "leading_space": lambda text: text[:1] == " ",
    "starts_with_quote": lambda text: text.lstrip()[:1] in ('"', "“"),
    "unbalanced_double_quote": lambda text: (
        (text.count('"') + text.count("“") + text.count("”")) % 2 == 1
    ),
    "replacement_char": lambda text: "�" in text,
    "lowercase_sentence_start": lambda text: text.lstrip()[:1].islower(),
    "single_sentence": lambda text: len(re.findall(r"[.!?]+(?=\s|$)", text)) <= 1,
    "run_on_over_40_words": lambda text: (
        len(text.split()) > 40 and len(re.findall(r"[.!?](?=\s|$)", text)) <= 1
    ),
    "arabic_numeral": lambda text: bool(re.search(r"\d", text)),
    "space_before_punctuation": lambda text: bool(re.search(r"\s+[.,;:!?]", text)),
    "repeated_5gram": lambda text: _has_repeated_ngram(text, 5),
}


def _read_jsonl(path: Path) -> list[Row]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _has_repeated_ngram(text: str, size: int) -> bool:
    words = text.lower().split()
    if len(words) < size:
        return False
    grams = Counter(tuple(words[i : i + size]) for i in range(len(words) - size + 1))
    return any(count > 1 for count in grams.values())


def _numeric_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p90": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p90": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))],
        "min": ordered[0],
        "max": ordered[-1],
    }


def _rate(rows: Sequence[Row], predicate: Callable[[str], bool]) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if predicate(str(row.get("stegotext") or ""))) / len(rows)


def _judge_means(rows: Sequence[Row]) -> dict[str, float | None]:
    keys = (
        "geval_overall",
        "geval_coherence",
        "geval_relevance",
        "geval_fluency",
        "geval_factual_consistency",
        "thread_grounded_factuality",
        "self_consistency",
    )
    means: dict[str, float | None] = {}
    for key in keys:
        scored = [float(row[key]) for row in rows if row.get(key) is not None]
        means[key] = statistics.fmean(scored) if scored else None
    return means


def _method_profile(rows: Sequence[Row]) -> dict[str, Any]:
    texts = [str(row.get("stegotext") or "") for row in rows]
    return {
        "rows": len(rows),
        "unique_texts": len(set(texts)),
        "posts": len({str(row.get("post_id")) for row in rows}),
        "word_count": _numeric_summary([float(row.get("word_count") or 0) for row in rows]),
        "perplexity_gpt2": _numeric_summary(
            [float(row["perplexity_gpt2"]) for row in rows if row.get("perplexity_gpt2")]
        ),
        "embedded_bits": _numeric_summary([float(row.get("embedded_bits") or 0) for row in rows]),
        "decode_ok": sum(1 for row in rows if row.get("decode_ok")),
        "recovery_sources": dict(Counter(str(row.get("recovery_source")) for row in rows)),
        "surface_rates": {name: _rate(rows, test) for name, test in SURFACE_TESTS.items()},
        "judge_means": _judge_means(rows),
    }


def _opener_recurrence(rows: Sequence[Row]) -> dict[str, Any]:
    unique = list(dict.fromkeys(str(row.get("stegotext") or "") for row in rows))
    counts = {
        phrase: sum(1 for text in unique if phrase in text.lower()) for phrase in OPENER_PHRASES
    }
    return {"unique_texts": len(unique), "phrase_counts": counts}


def _harvest_human_comments(rows: Iterable[Row], min_words: int = 10) -> list[str]:
    harvested: list[str] = []
    for context in {str(row.get("thread_context") or "") for row in rows}:
        for line in context.split("\n"):
            stripped = line.strip()
            if len(stripped.split()) >= min_words:
                harvested.append(stripped)
    return list(dict.fromkeys(harvested))


def _text_features(text: str) -> dict[str, float]:
    words = text.split()
    count = len(words)
    sentences = max(1, len(re.findall(r"[.!?](?=\s|$)", text)))
    return {
        "word_count": float(count),
        "words_per_sentence": count / sentences,
        "type_token_ratio": len({word.lower() for word in words}) / max(1, count),
        "has_digit": float(bool(re.search(r"\d", text))),
        "leading_space": float(text[:1] == " "),
    }


def _rank_auc(positive: Sequence[float], negative: Sequence[float]) -> float:
    if not positive or not negative:
        return 0.5
    ordered = sorted(negative)
    total = 0.0
    for value in positive:
        low = bisect.bisect_left(ordered, value)
        high = bisect.bisect_right(ordered, value)
        total += low + 0.5 * (high - low)
    return total / (len(positive) * len(negative))


def _separability(rows: Sequence[Row], human: Sequence[str]) -> dict[str, float]:
    stego_features = [_text_features(str(row.get("stegotext") or "")) for row in rows]
    human_features = [_text_features(text) for text in human]
    scores: dict[str, float] = {}
    for name in ("word_count", "words_per_sentence", "type_token_ratio", "has_digit"):
        auc = _rank_auc([f[name] for f in stego_features], [f[name] for f in human_features])
        scores[name] = max(auc, 1.0 - auc)
    lead = _rank_auc(
        [f["leading_space"] for f in stego_features], [f["leading_space"] for f in human_features]
    )
    scores["leading_space"] = max(lead, 1.0 - lead)
    return scores


def _failure_categories(attempts: Sequence[Row]) -> dict[str, int]:
    categories: Counter[str] = Counter()
    for attempt in attempts:
        if attempt.get("accepted"):
            categories["accepted"] += 1
            continue
        reason = str(attempt.get("reason") or "unknown")
        categories["failed: " + (re.split(r"[:{]", reason)[0].strip() or "unknown")] += 1
    return dict(categories.most_common())


def _attempt_summary(attempts: Sequence[Row]) -> dict[str, Any]:
    accepted = [attempt for attempt in attempts if attempt.get("accepted")]
    payload_bits = [float(a.get("encoded_bits") or 0) for a in accepted]
    return {
        "attempts": len(attempts),
        "accepted": len(accepted),
        "failure_rate": (len(attempts) - len(accepted)) / len(attempts) if attempts else 0.0,
        "failure_categories": _failure_categories(attempts),
        "accepted_encoded_bits": _numeric_summary(payload_bits),
        "distinct_payload_chunks": len({str(a.get("payload")) for a in accepted}),
        "params_used": accepted[0].get("params_used") if accepted else None,
    }


def _recoverable_bits(comment_choices: int, angle_choices: int) -> int:
    comment_bits = int(math.log2(comment_choices)) if comment_choices > 1 else 0
    angle_bits = int(math.log2(angle_choices)) if angle_choices > 1 else 0
    return comment_bits + angle_bits


def _source_artifact_index(source_dir: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in sorted((source_dir / "output-results").glob("*.json")):
        index.setdefault(path.name.split("_")[0], path)
    return index


def _artifact_channel_bits(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entry: Row = payload[0] if isinstance(payload, list) else payload
    embedding: Row = entry.get("embedding") or {}
    post: Row = entry.get("post") or {}
    return {
        "angle_choices": len(post.get("angles") or []),
        "comment_bits_used": int((embedding.get("commentEmbedding") or {}).get("bitsCount") or 0),
        "angle_bits_used": int((embedding.get("angleEmbedding") or {}).get("bitsCount") or 0),
        "remaining_bits_unembedded": int(embedding.get("remainingBitsUnembedded") or 0),
    }


def _capacity_row(row: Row, source_path: Path) -> dict[str, float] | None:
    report: Row = row.get("selection_capacity_report") or {}
    if not report:
        return None
    channels = _artifact_channel_bits(source_path)
    return {
        "reported": float(row.get("selection_bits") or 0),
        "recomputed": float(
            _recoverable_bits(int(report.get("comment_choices") or 0), channels["angle_choices"])
        ),
        "physical": float(channels["comment_bits_used"] + channels["angle_bits_used"]),
        "unembedded": float(channels["remaining_bits_unembedded"]),
        "angle_channel_dropped": float(
            int(report.get("tangent_choices") or 0) == 0 and channels["angle_bits_used"] > 0
        ),
    }


def _post_clustered_means(pairs: Sequence[tuple[str, dict[str, float]]]) -> dict[str, float]:
    """Equal weight per post, matching the inference unit of the 2026-07-26 audit."""
    by_post: dict[str, list[dict[str, float]]] = {}
    for post_id, values in pairs:
        by_post.setdefault(post_id, []).append(values)
    means: dict[str, float] = {}
    for key in ("reported", "recomputed", "physical", "unembedded"):
        per_post = [statistics.fmean([v[key] for v in values]) for values in by_post.values()]
        means[key] = statistics.fmean(per_post) if per_post else 0.0
    means["posts"] = float(len(by_post))
    return means


def _capacity_audit(rows: Sequence[Row], source_dir: Path) -> dict[str, Any]:
    """Compare the artifact's stored capacity with one recomputed including the angle channel."""
    index = _source_artifact_index(source_dir)
    measured: list[tuple[str, dict[str, float]]] = []
    for row in rows:
        path = index.get(str(row.get("post_id")))
        values = _capacity_row(row, path) if path is not None else None
        if values is not None:
            measured.append((str(row.get("post_id")), values))
    return {
        "rows_matched": len(measured),
        "rows_with_dropped_angle_channel": int(
            sum(values["angle_channel_dropped"] for _, values in measured)
        ),
        "reported_recoverable_bits": _numeric_summary([v["reported"] for _, v in measured]),
        "recomputed_recoverable_bits": _numeric_summary([v["recomputed"] for _, v in measured]),
        "physical_bits_used": _numeric_summary([v["physical"] for _, v in measured]),
        "compressed_bits_left_unembedded": _numeric_summary([v["unembedded"] for _, v in measured]),
        "post_clustered_means": _post_clustered_means(measured),
    }


def _capacity_sweep(path: Path) -> list[Row]:
    entries: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry: Row = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.append(
            {
                "bytes": entry.get("bytes"),
                "status": entry.get("status"),
                "bpw_estimate": entry.get("bpw_estimate"),
                "decode_ok": entry.get("decode_ok"),
                "secret_matches": entry.get("secret_matches"),
                "error_head": str(entry.get("error") or "")[:200] or None,
            }
        )
    return entries


def _build_report(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    rows = _read_jsonl(run_dir / "comparison_dataset" / "paired_rows.jsonl")
    attempts = _read_jsonl(run_dir / "results.jsonl")
    human = _harvest_human_comments(rows)
    methods = sorted({str(row.get("method")) for row in rows})
    report: dict[str, Any] = {
        "run_dir": str(run_dir),
        "paired_rows": len(rows),
        "human_reference_comments": len(human),
        "zlg_attempts": _attempt_summary(attempts),
        "methods": {
            method: _method_profile([row for row in rows if row.get("method") == method])
            for method in methods
        },
        "opener_recurrence": {
            method: _opener_recurrence([row for row in rows if row.get("method") == method])
            for method in methods
        },
        "separability_vs_human": {
            method: _separability([row for row in rows if row.get("method") == method], human)
            for method in methods
        },
    }
    if args.source_run_dir:
        report["capacity_audit"] = _capacity_audit(
            [row for row in rows if row.get("method") == "our_method"],
            Path(args.source_run_dir).resolve(),
        )
    if args.capacity_sweep:
        report["zlg_capacity_sweep"] = _capacity_sweep(Path(args.capacity_sweep).resolve())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="ZLG comparison run directory")
    parser.add_argument("--source-run-dir", help="e2e run dir holding output-results/ artifacts")
    parser.add_argument("--capacity-sweep", help="results.jsonl of a ZLG capacity sweep")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = _build_report(args)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
