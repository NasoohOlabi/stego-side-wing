"""Cross cached synthetic-detection outcomes with tangent database quality."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

VERSION = "tangent_drift_attribution_v1"


def _reason_category(reason: str) -> str:
    text = reason.casefold()
    groups = (
        ("off_topic", ("off-topic", "off topic", "irrelevant", "context")),
        ("incoherent", ("incoherent", "nonsens", "confus", "doesn't make sense")),
        ("repetition", ("repet", "redundan")),
        ("style", ("synthetic", "generated", "unnatural", "robotic", "formal")),
        ("brevity", ("brief", "short", "vague", "generic")),
    )
    return next((name for name, words in groups if any(word in text for word in words)), "other")


def _post_reports(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        report = row.get("tangent_db_report")
        if row.get("method") == "our_method" and isinstance(report, dict):
            grouped.setdefault(str(row.get("post_id")), []).append(_report_metrics(report))
    return {post_id: _average_reports(reports) for post_id, reports in grouped.items()}


def _report_metrics(report: dict[str, Any]) -> dict[str, Any]:
    raw_relevance = report.get("relevance")
    relevance: dict[str, Any] = raw_relevance if isinstance(raw_relevance, dict) else {}
    scores = [
        float(value)
        for value in relevance.get("scores_kept", [])
        if isinstance(value, (int, float))
    ]
    raw_source = report.get("source_mix_kept")
    source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
    return {
        "relevance_mean": statistics.fmean(scores) if scores else relevance.get("mean"),
        "source_counts": source,
    }


def _average_reports(reports: list[dict[str, Any]]) -> dict[str, float]:
    def mean(key: str) -> float:
        values = [float(row[key]) for row in reports if isinstance(row.get(key), (int, float))]
        return statistics.fmean(values) if values else 0.0

    source = {
        name: statistics.fmean(float(row["source_counts"].get(name, 0)) for row in reports)
        for name in ("post", "comments", "search_results")
    }
    total = sum(source.values())
    return {
        "relevance_mean": mean("relevance_mean"),
        "search_share": source["search_results"] / total if total else 0.0,
    }


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"posts": 0, "search_share_mean": None, "relevance_mean": None}
    return {
        "posts": len(rows),
        "search_share_mean": statistics.fmean(row["search_share"] for row in rows),
        "relevance_mean": statistics.fmean(row["relevance_mean"] for row in rows),
    }


def analyze(paired: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> dict[str, Any]:
    reports = _post_reports(paired)
    detected_posts = {
        str(row.get("post_id"))
        for row in judgments
        if row.get("valid") is True
        and row.get("correct") is True
    }
    detected = [report for post_id, report in reports.items() if post_id in detected_posts]
    undetected = [report for post_id, report in reports.items() if post_id not in detected_posts]
    reasons = Counter(
        _reason_category(_judgment_reason(row))
        for row in judgments
        if row.get("valid") is True
        and row.get("correct") is True
        and str(row.get("post_id")) in reports
    )
    return {
        "version": VERSION,
        "inference_unit": "unique_post_id",
        "posts_with_tangent_reports": len(reports),
        "detected": _group_summary(detected),
        "not_detected": _group_summary(undetected),
        "detected_reason_categories": dict(sorted(reasons.items())),
    }


def _judgment_reason(row: dict[str, Any]) -> str:
    reason = row.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    raw = row.get("raw_response")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(parsed, dict):
            return str(parsed.get("rationale") or "")
    return ""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paired-rows", type=Path, required=True)
    parser.add_argument("--sus-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(_load_jsonl(args.paired_rows), _load_jsonl(args.sus_results))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
