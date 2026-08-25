#!/usr/bin/env python3
"""Pair balanced vs barb e2e lanes into comparison/ under a BARB run dir."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from workflows.pipelines.stego_contextuality import (  # noqa: E402
    SAFE_UNIVERSAL_PATTERNS,
    tokenize_content_words,
)

WORD_RE = re.compile(r"[A-Za-z0-9']+")
AFFECT_OPINION_CUES = (
    "hate",
    "love",
    "ridiculous",
    "absurd",
    "wild",
    "insane",
    "furious",
    "angry",
    "excited",
    "thrilled",
    "doubt",
    "skeptical",
    "outraged",
    "gross",
    "pathetic",
    "brilliant",
    "stupid",
    "nightmare",
    "joke",
    "mess",
    "bullshit",
    "bs",
    "ugh",
    "wow",
    "yikes",
    "nope",
    "damn",
    "hell",
    "crying",
    "laughing",
    "smh",
    "lmao",
    "imo",
    "honestly",
    "seriously",
    "obviously",
    "clearly",
    "refuses",
    "can't stand",
    "cant stand",
    "fed up",
    "sick of",
)
CONTROL_METHOD = "balanced"
TREATMENT_METHOD = "barb"


def pair_key(post_id: str, sample_index: int | str | None) -> tuple[str, int]:
    """Canonical join key for balanced ↔ barb sample pairing."""
    try:
        index = int(sample_index) if sample_index is not None else 0
    except (TypeError, ValueError):
        index = 0
    return str(post_id), index


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _lane_summary(run_dir: Path, method: str) -> dict[str, Any]:
    """Rebuild the minimal E2E summary omitted by the resumable campaign runner."""
    campaign = _read_json(run_dir / "campaign.json")
    entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for sample_index, post_id in enumerate(campaign.get("post_ids") or []):
        label = f"{post_id}_version_{method}_{sample_index:04d}.json"
        output = run_dir / method / "output-results" / label
        failure = run_dir / method / "failures" / label
        if output.is_file():
            entries.append({"post_id": post_id, "sample_index": sample_index, "output_file": str(output)})
        elif failure.is_file():
            payload = _read_json(failure)
            failures.append({
                "post_id": post_id,
                "sample_index": sample_index,
                "output_file": None,
                "failure_code": payload.get("failure_code"),
                "error": payload.get("error"),
            })
    return {"entries": entries, "failures": failures}


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _safe_universal_hit(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    return any(pattern in normalized for pattern in SAFE_UNIVERSAL_PATTERNS)


def _has_numeral(text: str) -> bool:
    return bool(re.search(r"\d", text))


def _has_affect_opinion_cue(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    return any(cue in normalized for cue in AFFECT_OPINION_CUES)


def _title_selftext_from_post(post: dict[str, Any] | None) -> str:
    if not isinstance(post, dict):
        return ""
    parts: list[str] = []
    for key in ("title", "selftext"):
        value = post.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return " ".join(parts)


def _proper_noun_tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text)}


def human_lean_features(stego_text: str, *, post: dict[str, Any] | None) -> dict[str, bool]:
    """Lightweight human-lean features for the BARB comparison viewer."""
    thread = _title_selftext_from_post(post)
    thread_proper = _proper_noun_tokens(thread)
    text_proper = _proper_noun_tokens(stego_text)
    content_overlap = set(tokenize_content_words(stego_text)) & set(
        tokenize_content_words(thread)
    )
    return {
        "has_thread_proper_noun_overlap": bool(thread_proper & text_proper) or bool(content_overlap),
        "has_numeral": _has_numeral(stego_text),
        "safe_universal_hit": _safe_universal_hit(stego_text),
        "has_affect_opinion_cue": _has_affect_opinion_cue(stego_text),
    }


def _load_stego_text(output_file: str | None) -> str:
    if not output_file:
        return ""
    path = Path(output_file)
    if not path.is_file():
        return ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        text = payload[0].get("stegoText")
        return text if isinstance(text, str) else ""
    if isinstance(payload, dict):
        text = payload.get("stego_text") or payload.get("stegoText")
        return text if isinstance(text, str) else ""
    return ""


def _load_post_snapshot(output_file: str | None) -> dict[str, Any] | None:
    if not output_file:
        return None
    path = Path(output_file)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        post = payload[0].get("post")
        return post if isinstance(post, dict) else None
    return None


def _index_lane_records(summary: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in summary.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        key = pair_key(str(entry.get("post_id") or ""), entry.get("sample_index"))
        indexed[key] = {
            "post_id": key[0],
            "sample_index": key[1],
            "encode_succeeded": True,
            "receiver_match": True,
            "itt_success": True,
            "output_file": entry.get("output_file"),
            "failure_code": None,
            "error": None,
        }
    for failure in summary.get("failures") or []:
        if not isinstance(failure, dict):
            continue
        key = pair_key(str(failure.get("post_id") or ""), failure.get("sample_index"))
        if key in indexed:
            continue
        indexed[key] = {
            "post_id": key[0],
            "sample_index": key[1],
            "encode_succeeded": False,
            "receiver_match": False,
            "itt_success": False,
            "output_file": failure.get("output_file"),
            "failure_code": failure.get("failure_code"),
            "error": failure.get("error"),
        }
    return indexed


def build_method_row(
    *,
    method: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    stego_text = _load_stego_text(
        str(record["output_file"]) if record.get("output_file") else None
    )
    post = _load_post_snapshot(str(record["output_file"]) if record.get("output_file") else None)
    features = human_lean_features(stego_text, post=post)
    return {
        "pair_id": f"{record['post_id']}:{record['sample_index']}",
        "post_id": record["post_id"],
        "sample_index": record["sample_index"],
        "method": method,
        "stego_text": stego_text,
        "stegotext": stego_text,
        "encode_succeeded": bool(record.get("encode_succeeded")),
        "receiver_match": bool(record.get("receiver_match")),
        "itt_success": bool(record.get("itt_success")),
        "word_count": _word_count(stego_text),
        "failure_code": record.get("failure_code"),
        "error": record.get("error"),
        "features": features,
    }


def join_variant_lanes(
    balanced_summary: dict[str, Any],
    barb_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Inner-join balanced and barb lanes by (post_id, sample_index); emit one row per method."""
    left = _index_lane_records(balanced_summary)
    right = _index_lane_records(barb_summary)
    shared = sorted(set(left) & set(right))
    rows: list[dict[str, Any]] = []
    for key in shared:
        rows.append(build_method_row(method=CONTROL_METHOD, record=left[key]))
        rows.append(build_method_row(method=TREATMENT_METHOD, record=right[key]))
    return rows


def _feature_rate(rows: list[dict[str, Any]], method: str, feature: str) -> float | None:
    method_rows = [row for row in rows if row.get("method") == method]
    if not method_rows:
        return None
    hits = sum(1 for row in method_rows if (row.get("features") or {}).get(feature))
    return hits / len(method_rows)


def _mean_word_count(rows: list[dict[str, Any]], method: str) -> float | None:
    values = [int(row["word_count"]) for row in rows if row.get("method") == method]
    return statistics.fmean(values) if values else None


def _rate(rows: list[dict[str, Any]], method: str, field: str) -> float | None:
    method_rows = [row for row in rows if row.get("method") == method]
    if not method_rows:
        return None
    return sum(1 for row in method_rows if row.get(field)) / len(method_rows)


def _sign_test_stub(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """Paired sign counts for a boolean field (balanced vs barb); p-value left unset."""
    by_key: dict[tuple[str, int], dict[str, bool]] = {}
    for row in rows:
        key = pair_key(str(row.get("post_id") or ""), row.get("sample_index"))
        by_key.setdefault(key, {})[str(row.get("method"))] = bool(row.get(field))
    wins = Counter()
    ties = 0
    for outcomes in by_key.values():
        left = outcomes.get(CONTROL_METHOD)
        right = outcomes.get(TREATMENT_METHOD)
        if left is None or right is None:
            continue
        if left == right:
            ties += 1
        elif right and not left:
            wins[TREATMENT_METHOD] += 1
        elif left and not right:
            wins[CONTROL_METHOD] += 1
    return {
        "field": field,
        "paired_n": len(by_key),
        "ties": ties,
        "barb_wins": wins[TREATMENT_METHOD],
        "balanced_wins": wins[CONTROL_METHOD],
        "p_value": None,
        "note": "sign-test stub; p-value not computed offline",
    }


def build_summary(rows: list[dict[str, Any]], *, run_dir: Path) -> dict[str, Any]:
    paired_n = len({pair_key(str(r["post_id"]), r["sample_index"]) for r in rows})
    return {
        "run_dir": str(run_dir),
        "paired_posts": paired_n,
        "row_count": len(rows),
        "methods": {
            CONTROL_METHOD: {
                "itt_success_rate": _rate(rows, CONTROL_METHOD, "itt_success"),
                "receiver_match_rate": _rate(rows, CONTROL_METHOD, "receiver_match"),
                "mean_word_count": _mean_word_count(rows, CONTROL_METHOD),
                "safe_universal_rate": _feature_rate(
                    rows, CONTROL_METHOD, "safe_universal_hit"
                ),
                "thread_specificity_rate": _feature_rate(
                    rows, CONTROL_METHOD, "has_thread_proper_noun_overlap"
                ),
                "affect_opinion_rate": _feature_rate(
                    rows, CONTROL_METHOD, "has_affect_opinion_cue"
                ),
            },
            TREATMENT_METHOD: {
                "itt_success_rate": _rate(rows, TREATMENT_METHOD, "itt_success"),
                "receiver_match_rate": _rate(rows, TREATMENT_METHOD, "receiver_match"),
                "mean_word_count": _mean_word_count(rows, TREATMENT_METHOD),
                "safe_universal_rate": _feature_rate(
                    rows, TREATMENT_METHOD, "safe_universal_hit"
                ),
                "thread_specificity_rate": _feature_rate(
                    rows, TREATMENT_METHOD, "has_thread_proper_noun_overlap"
                ),
                "affect_opinion_rate": _feature_rate(
                    rows, TREATMENT_METHOD, "has_affect_opinion_cue"
                ),
            },
        },
        "sign_tests": [
            _sign_test_stub(rows, "itt_success"),
            _sign_test_stub(rows, "receiver_match"),
        ],
    }


def write_comparison_dataset(run_dir: Path) -> dict[str, Any]:
    balanced_path = run_dir / CONTROL_METHOD / "summary.json"
    barb_path = run_dir / TREATMENT_METHOD / "summary.json"
    balanced = _read_json(balanced_path) if balanced_path.is_file() else _lane_summary(run_dir, CONTROL_METHOD)
    barb = _read_json(barb_path) if barb_path.is_file() else _lane_summary(run_dir, TREATMENT_METHOD)
    rows = join_variant_lanes(balanced, barb)
    comparison_dir = run_dir / "comparison"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    rows_path = comparison_dir / "paired_rows.jsonl"
    rows_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    summary = build_summary(rows, run_dir=run_dir)
    summary_path = comparison_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The generic LLM-judge runner consumes this conventional location; retain the
    # viewer-facing `comparison/` directory above as the BARB public artifact.
    judge_dir = run_dir / "comparison_dataset"
    judge_dir.mkdir(parents=True, exist_ok=True)
    (judge_dir / "paired_rows.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    manifest = {
        "run_dir": str(run_dir),
        "control": CONTROL_METHOD,
        "treatment": TREATMENT_METHOD,
        "paired_rows": str(rows_path),
        "summary": str(summary_path),
        "paired_posts": summary["paired_posts"],
        "row_count": summary["row_count"],
    }
    manifest_path = comparison_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build BARB vs balanced paired comparison.")
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="metrics/experiments/barb/runs/<run_id>",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    barb_root = (_REPO_ROOT / "metrics" / "experiments" / "barb" / "runs").resolve()
    if not str(run_dir).startswith(str(barb_root)):
        raise SystemExit(f"Run dir must be under {barb_root}, got {run_dir}")
    manifest = write_comparison_dataset(run_dir)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
