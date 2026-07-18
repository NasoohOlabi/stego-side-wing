"""Run a fresh post through research/angles and scan every stego angle."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from workflows.pipelines.data_load import DataLoadPipeline  # noqa: E402
from workflows.pipelines.gen_angles import GenAnglesPipeline  # noqa: E402
from workflows.pipelines.research import ResearchPipeline  # noqa: E402
from workflows.pipelines.stego import StegoPipeline  # noqa: E402
from workflows.utils.protocol_utils import stable_hash  # noqa: E402
from workflows.utils.stego_codec import (  # noqa: E402
    angle_bits_for_index,
    comment_selection_bit_width,
    encode_int,
    flatten_comments,
    flatten_nested_angles,
)

RUNS_ROOT = _REPO_ROOT / "metrics" / "angle_scan_runs"
DATASET_DIR = _REPO_ROOT / "datasets" / "news_cleaned"
E2E_RUNS_ROOT = _REPO_ROOT / "metrics" / "e2e_runs"


@contextmanager
def _temporary_env(overrides: dict[str, str]):
    old_values = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _tokenize(text: Any) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{2,}", str(text).lower())
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "have",
        "has",
        "about",
        "into",
        "their",
        "there",
        "would",
        "could",
        "should",
    }
    return {word for word in words if word not in stop}


def _latest_e2e_post_ids(root: Path = E2E_RUNS_ROOT) -> set[str]:
    candidates = sorted(
        [path for path in root.glob("*/post_ids.json") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return set()
    payload = _read_json(candidates[0])
    post_ids = payload.get("post_ids", [])
    return {str(item) for item in post_ids if isinstance(item, str)}


def _angle_scan_post_ids(root: Path = RUNS_ROOT) -> set[str]:
    used: set[str] = set()
    for path in root.glob("*/angle_scan_summary.json"):
        try:
            payload = _read_json(path)
        except Exception:
            continue
        post_id = payload.get("post_id")
        if isinstance(post_id, str) and post_id:
            used.add(post_id)
    for path in root.glob("*/stage_reports.json"):
        try:
            payload = _read_json(path)
        except Exception:
            continue
        post_id = payload.get("post_id")
        if isinstance(post_id, str) and post_id:
            used.add(post_id)
    return used


def select_unused_post_id(
    dataset_dir: Path = DATASET_DIR,
    e2e_runs_root: Path = E2E_RUNS_ROOT,
    angle_scan_runs_root: Path = RUNS_ROOT,
) -> str:
    used = _latest_e2e_post_ids(e2e_runs_root) | _angle_scan_post_ids(angle_scan_runs_root)
    for path in sorted(dataset_dir.glob("*.json")):
        if path.stem not in used:
            return path.stem
    raise ValueError(f"No unused post found in {dataset_dir}")


def _load_post(
    post_id: str, dataset_dir: Path, run_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset_path = dataset_dir / f"{post_id}.json"
    if not dataset_path.exists():
        raise FileNotFoundError(dataset_path)
    original = _read_json(dataset_path)
    pipeline = DataLoadPipeline()
    try:
        preview = pipeline.preview_post(original, use_cache=True)
        post = preview["post"]
        report = preview["report"]
        if not post.get("selftext"):
            post = original
            report = dict(report, fallback="local_dataset_selftext")
    except Exception as exc:
        post = original
        report = {
            "post_id": post_id,
            "fallback": "local_dataset",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
    _write_json(run_dir / "data_load_post.json", post)
    return post, report


def _alignment_report(post: dict[str, Any], research_report: dict[str, Any]) -> dict[str, Any]:
    post_tokens = _tokenize(
        " ".join(
            [
                str(post.get("title", "")),
                str(post.get("selftext", "")),
                " ".join(
                    str(c.get("body", "")) for c in flatten_comments(post.get("comments", []))[:20]
                ),
            ]
        )
    )
    search_text = " ".join(str(item) for item in research_report.get("search_results", []))
    search_tokens = _tokenize(search_text)
    angles = flatten_nested_angles(post)
    angle_scores: list[dict[str, Any]] = []
    for angle in angles:
        angle_text = " ".join(
            str(angle.get(key, "")) for key in ("category", "tangent", "source_quote")
        )
        angle_tokens = _tokenize(angle_text)
        overlap = angle_tokens & post_tokens
        search_overlap = angle_tokens & search_tokens
        denom = max(1, len(angle_tokens))
        angle_scores.append(
            {
                "idx": angle.get("idx"),
                "category": angle.get("category"),
                "post_overlap_ratio": len(overlap) / denom,
                "search_overlap_ratio": len(search_overlap) / denom,
                "overlap_tokens": sorted(overlap)[:20],
            }
        )
    mean_post = sum(item["post_overlap_ratio"] for item in angle_scores) / max(1, len(angle_scores))
    mean_search = sum(item["search_overlap_ratio"] for item in angle_scores) / max(
        1, len(angle_scores)
    )
    return {
        "post_id": post.get("id"),
        "post_token_count": len(post_tokens),
        "search_token_count": len(search_tokens),
        "angle_count": len(angles),
        "mean_angle_post_overlap": mean_post,
        "mean_angle_search_overlap": mean_search,
        "likely_mismatch": bool(angles and mean_post < 0.03 and mean_search < 0.03),
        "best_angles": sorted(
            angle_scores, key=lambda item: item["post_overlap_ratio"], reverse=True
        )[:10],
        "worst_angles": sorted(angle_scores, key=lambda item: item["post_overlap_ratio"])[:10],
    }


def _comment_bits(post: dict[str, Any], comment_index: int | None) -> str:
    width = comment_selection_bit_width(post)
    if comment_index is None:
        return "0" * width
    comment_count = len(flatten_comments(post.get("comments", [])))
    selection_index = comment_index + 1
    if selection_index > comment_count:
        raise ValueError(f"--comment-index {comment_index} exceeds {comment_count} comments")
    return encode_int(selection_index, comment_count)


def build_angle_scan_bits(
    post: dict[str, Any], angle_index: int, comment_index: int | None = None
) -> str:
    angles = flatten_nested_angles(post)
    return _comment_bits(post, comment_index) + angle_bits_for_index(angle_index, len(angles))


def _classify_scan_result(result: dict[str, Any]) -> str:
    if result.get("succeeded"):
        return "success"
    error = str(result.get("error") or "")
    if "valid JSON" in error:
        return "stego_invalid_json"
    candidates = result.get("validation_details", {}).get("candidates", [])
    if any("decode_mismatch" in item.get("rejection_reasons", []) for item in candidates):
        return "decode_mismatch"
    if any(item.get("context_gate", {}).get("passes") is False for item in candidates):
        return "contextuality_reject"
    return "generation_failure"


def _scan_row(
    *,
    post: dict[str, Any],
    angle: dict[str, Any],
    bits: str,
    result: dict[str, Any],
    elapsed_ms: int,
) -> dict[str, Any]:
    candidates = result.get("validation_details", {}).get("candidates", [])
    first_candidate = candidates[0] if candidates else {}
    return {
        "post_id": post.get("id"),
        "angle_index": angle.get("idx"),
        "category": angle.get("category"),
        "source_quote": angle.get("source_quote"),
        "tangent": angle.get("tangent"),
        "binary_selection_bits": bits,
        "comment_bits": result.get("comment_bits", ""),
        "angle_bits": result.get("angle_bits", ""),
        "succeeded": bool(result.get("succeeded")),
        "failure_code": _classify_scan_result(result),
        "retry_count": result.get("retry_count"),
        "stegoText": result.get("stego_text", ""),
        "decoded_index": first_candidate.get("decoded_index"),
        "strict_decoded_index": first_candidate.get("strict_decoded_index"),
        "decoded_angle": first_candidate.get("decoded_angle"),
        "context_gate": first_candidate.get("context_gate"),
        "rejection_reasons": first_candidate.get("rejection_reasons", []),
        "error": result.get("error"),
        "elapsed_ms": elapsed_ms,
    }


def summarize_scan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    failures = [row for row in rows if not row.get("succeeded")]
    codes = Counter(str(row.get("failure_code")) for row in rows)
    categories = Counter(str(row.get("category") or "<unknown>") for row in failures)
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "total_angles": total,
        "succeeded": total - len(failures),
        "failed": len(failures),
        "success_rate": (total - len(failures)) / total if total else 0.0,
        "json_fail_rate": codes["stego_invalid_json"] / total if total else 0.0,
        "decode_mismatch_rate": codes["decode_mismatch"] / total if total else 0.0,
        "contextuality_rejection_rate": codes["contextuality_reject"] / total if total else 0.0,
        "failure_counts": dict(codes),
        "top_bad_categories": categories.most_common(10),
        "best_examples": [row for row in rows if row.get("succeeded")][:5],
        "worst_examples": failures[:5],
    }


def _existing_rows(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[int, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        idx = item.get("angle_index")
        if isinstance(idx, int):
            rows[idx] = item
    return rows


def _preview_angles(
    post: dict[str, Any],
    *,
    angles_mode: str,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    if angles_mode == "configured":
        return GenAnglesPipeline().preview_post(post, allow_fallback=allow_fallback)
    with _temporary_env({"WORKFLOW_ANGLES_GENERATION_MODE": angles_mode}):
        return GenAnglesPipeline().preview_post(post, allow_fallback=allow_fallback)


def run_angle_scan(args: argparse.Namespace) -> dict[str, Any]:
    if args.llm_backend == "qwen":
        os.environ["WORKFLOW_LLM_BACKEND"] = "lm_studio"
        os.environ.setdefault("WORKFLOW_LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
        os.environ.setdefault("ANGLES_MODEL", "qwen/qwen3.5-9b")

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    post_id = args.post_id or select_unused_post_id(DATASET_DIR, E2E_RUNS_ROOT)

    post, data_report = _load_post(post_id, DATASET_DIR, run_dir)
    try:
        research_preview = ResearchPipeline().preview_post(post, force=True)
        researched_post = research_preview["post"]
        research_report = research_preview["report"]
    except Exception as exc:
        researched_post = dict(post)
        search_results = researched_post.get("search_results")
        if not isinstance(search_results, list):
            search_results = []
            researched_post["search_results"] = search_results
        research_report = {
            "post_id": post_id,
            "skipped_due_to_error": True,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "search_results": search_results,
            "search_results_hash": stable_hash(search_results),
            "search_results_count": len(search_results),
        }
    _write_json(run_dir / "research_post.json", researched_post)

    angles_preview = _preview_angles(
        researched_post,
        angles_mode=args.angles_mode,
        allow_fallback=True,
    )
    angled_post = angles_preview["post"]
    angles_report = angles_preview["report"]
    alignment = _alignment_report(angled_post, research_report)
    repeated_gen_angles = False
    if alignment["likely_mismatch"]:
        clean_input = dict(researched_post)
        clean_input.pop("angles", None)
        angles_preview = _preview_angles(
            clean_input,
            angles_mode=args.angles_mode,
            allow_fallback=True,
        )
        angled_post = angles_preview["post"]
        angles_report = angles_preview["report"]
        alignment = _alignment_report(angled_post, research_report)
        repeated_gen_angles = True

    _write_json(run_dir / "angled_post.json", angled_post)
    _write_json(
        run_dir / "stage_reports.json",
        {
            "run_id": run_id,
            "post_id": post_id,
            "data_load": data_report,
            "research": research_report,
            "gen_angles": angles_report,
            "angles_mode": args.angles_mode,
            "repeated_gen_angles": repeated_gen_angles,
            "hashes": {
                "post": stable_hash(post),
                "research_results": stable_hash(research_report.get("search_results", [])),
                "angles": stable_hash(angles_report.get("angles", [])),
            },
        },
    )
    _write_json(run_dir / "research_angle_alignment.json", alignment)

    rows_by_idx = _existing_rows(run_dir / "angle_scan.jsonl") if args.resume else {}
    stego = StegoPipeline()
    angles = flatten_nested_angles(angled_post)
    if args.limit_angles is not None:
        angles = angles[: args.limit_angles]
    for angle in angles:
        idx = int(angle["idx"])
        if idx in rows_by_idx:
            continue
        bits = build_angle_scan_bits(angled_post, idx, args.comment_index)
        t0 = time.perf_counter()
        result = stego.encode_binary_selection_bits(
            bits=bits,
            post=angled_post,
            tag=f"angle-scan-{idx}",
            max_retries=args.max_retries,
        )
        row = _scan_row(
            post=angled_post,
            angle=angle,
            bits=bits,
            result=result,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
        _append_jsonl(run_dir / "angle_scan.jsonl", row)
        rows_by_idx[idx] = row

    rows = [rows_by_idx[idx] for idx in sorted(rows_by_idx)]
    summary = summarize_scan(rows)
    summary.update({"run_id": run_id, "run_dir": str(run_dir.resolve()), "post_id": post_id})
    _write_json(run_dir / "angle_scan_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-id")
    parser.add_argument("--run-id")
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--comment-index", type=int)
    parser.add_argument("--limit-angles", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--llm-backend", choices=["qwen", "env"], default="env")
    parser.add_argument(
        "--angles-mode",
        choices=["extractive_zero_kld", "configured"],
        default="extractive_zero_kld",
        help="Use extractive genAngles by default to avoid the configured analyzer hang.",
    )
    args = parser.parse_args()
    summary = run_angle_scan(args)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
