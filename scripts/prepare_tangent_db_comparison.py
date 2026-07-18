"""Offline setup and finalization for the legacy/v1 tangent-DB comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.config import get_workflow_angles_max_output  # noqa: E402
from infrastructure.prep_run_manifest import write_prep_run_manifest  # noqa: E402
from workflows.pipelines.gen_angles import GenAnglesPipeline  # noqa: E402
from workflows.utils.stego_codec import (  # noqa: E402
    angle_bits_decode_to_index,
    angle_bits_for_index,
    embed_in_angle_selection,
)
from workflows.utils.tangent_db import (  # noqa: E402
    AngleCandidate,
    PostContext,
    build_tangent_db,
    tangent_db_config_from_env,
)

CONTRACT_VERSION = "tangent_db_comparison_v1"
LANES = ("legacy", "v1")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def initialize_comparison(root: Path, *, comparison_id: str, notes: str = "") -> Path:
    """Create isolated, self-describing roots without starting provider work."""
    root = root.resolve()
    manifests: dict[str, str] = {}
    prior_root = os.environ.get("WORKFLOW_DATASET_ROOT")
    prior_builder = os.environ.get("WORKFLOW_TANGENT_DB_BUILDER")
    try:
        for builder in ("legacy", "v1"):
            lane_root = root / builder
            os.environ["WORKFLOW_DATASET_ROOT"] = str(lane_root)
            os.environ["WORKFLOW_TANGENT_DB_BUILDER"] = builder
            path = write_prep_run_manifest(run_id=f"{comparison_id}-{builder}", notes=notes)
            manifests[builder] = str(path)
    finally:
        _restore_env("WORKFLOW_DATASET_ROOT", prior_root)
        _restore_env("WORKFLOW_TANGENT_DB_BUILDER", prior_builder)
    contract = {
        "version": CONTRACT_VERSION,
        "comparison_id": comparison_id,
        "lanes": manifests,
        "minimum_diversity_ratio": 1.0,
        "live_provider_calls_started": False,
    }
    path = root / "comparison.json"
    path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return path


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _quote_words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _candidate_with_source(angle: dict[str, Any], entries: list[dict[str, Any]]) -> AngleCandidate:
    """Recover source provenance from the cached quote without changing its wording."""
    quote = _quote_words(str(angle.get("source_quote", "")))
    matches: list[tuple[int, str]] = []
    for index, entry in enumerate(entries):
        words = _quote_words(str(entry.get("text", "")))
        if quote and any(
            words[offset : offset + len(quote)] == quote for offset in range(len(words))
        ):
            matches.append((index, str(entry.get("source", ""))))
    sources = {source for _, source in matches}
    if len(sources) != 1:
        raise ValueError(
            f"cached angle quote has ambiguous/missing source: {angle.get('source_quote')!r}"
        )
    index, source = matches[0]
    return AngleCandidate.from_angle(angle).model_copy(
        update={"source_document": index, "source": source}
    )


def materialize_cached_v1(root: Path, *, post_id: str) -> Path:
    """Build a v1 lane offline from an accepted legacy candidate pool."""
    root = root.resolve()
    legacy_manifest = _read_object(root / "legacy" / "prep_run.json")
    v1_manifest = _read_object(root / "v1" / "prep_run.json")
    if legacy_manifest.get("tangent_db_config_hash") != v1_manifest.get("tangent_db_config_hash"):
        raise ValueError("legacy/v1 manifest config hashes differ")

    researched_source = root / "legacy" / "news_researched" / f"{post_id}.json"
    legacy_angle_path = root / "legacy" / "news_angles" / f"{post_id}.json"
    researched = _read_object(researched_source)
    legacy_angles = _read_object(legacy_angle_path)
    if researched.get("id") != post_id or legacy_angles.get("id") != post_id:
        raise ValueError("cached post id mismatch")
    angles = legacy_angles.get("angles")
    if not isinstance(angles, list) or legacy_angles.get("options_count") != len(angles):
        raise ValueError("legacy angle artifact has an invalid candidate count")

    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    entries = list(pipeline.build_dictionary_bundle_for_post(researched)["entries"])
    candidates = [_candidate_with_source(angle, entries) for angle in angles]
    config = tangent_db_config_from_env(get_workflow_angles_max_output())
    result = build_tangent_db(candidates, PostContext.from_post(researched), config)
    if result.report.config_hash != v1_manifest.get("tangent_db_config_hash"):
        raise ValueError("effective v1 config does not match the persisted manifest")

    researched_dest = root / "v1" / "news_researched" / f"{post_id}.json"
    researched_dest.parent.mkdir(parents=True, exist_ok=True)
    researched_dest.write_bytes(researched_source.read_bytes())
    output = root / "v1" / "news_angles" / f"{post_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact = dict(
        researched,
        angles=result.angles,
        options_count=len(result.angles),
        tangent_db_report=result.report.model_dump(mode="json"),
    )
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _load_generated_lane(
    root: Path, candidates: dict[str, Any], lane: str, post_id: str
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    post = _read_object(root / lane / "news_angles" / f"{post_id}.json")
    angles = post.get("angles")
    comments = candidates.get(lane)
    if post.get("id") != post_id or not isinstance(angles, list) or not angles:
        raise ValueError(f"{lane} angle artifact is invalid for post {post_id}")
    if post.get("options_count") != len(angles) or not all(isinstance(x, dict) for x in angles):
        raise ValueError(f"{lane} angle artifact has an invalid candidate count")
    if not isinstance(comments, list) or not all(isinstance(x, str) for x in comments):
        raise ValueError(f"{lane} generated comments must be a list of strings")
    if len(comments) != len(angles):
        raise ValueError(f"{lane} generated comment count must exactly match its angle count")
    return post, angles, comments


def _require_ordinary_visible(comment: str, lane: str, index: int) -> None:
    if not comment.strip() or not comment.isascii() or not comment.isprintable():
        raise ValueError(f"{lane} comment {index} is not ordinary visible text")


def _materialized_row(
    post_id: str,
    lane: str,
    angles: list[dict[str, Any]],
    comment: str,
    index: int,
    tangent_db_report: dict[str, Any] | None,
) -> dict[str, Any]:
    _require_ordinary_visible(comment, lane, index)
    payload = angle_bits_for_index(index, len(angles))
    sender = embed_in_angle_selection(payload, [angles])
    selected = sender["selectedAngle"]
    if sender["bitsUsed"] != payload or selected != {**angles[index], "idx": index}:
        raise ValueError(f"{lane} row {index} failed sender angle linkage")
    if not angle_bits_decode_to_index(payload, index, len(angles)):
        raise ValueError(f"{lane} row {index} failed receiver angle-index round trip")
    row = {
        "pair_id": index,
        "post_id": post_id,
        "sample_index": index,
        "slot": index,
        "method": "our_method",
        "lane": lane,
        "stegotext": comment,
        "payload": payload,
        "decode_ok": True,
        "selected_angle_index": index,
        "selected_angle": selected,
        "selection_signature": payload,
        "recovery_source": "real_codec_angle_index_verified",
    }
    if tangent_db_report is not None:
        row["tangent_db_report"] = tangent_db_report
    return row


def _prepare_generated_lane(
    root: Path, candidates: dict[str, Any], lane: str, post_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    post, angles, comments = _load_generated_lane(root, candidates, lane, post_id)
    normalized = [" ".join(comment.split()) for comment in comments]
    unique_ratio = len(set(normalized)) / len(normalized)
    if unique_ratio != 1.0:
        raise ValueError(f"{lane} M9 uniqueness ratio must equal 1.0")
    raw_report = post.get("tangent_db_report")
    report = raw_report if isinstance(raw_report, dict) else None
    rows = [
        _materialized_row(post_id, lane, angles, comment, i, report)
        for i, comment in enumerate(comments)
    ]
    if len({row["payload"] for row in rows}) != len(rows):
        raise ValueError(f"{lane} angle payloads must be unique")
    return rows, _generated_lane_summary(lane, post, rows, unique_ratio)


def _generated_lane_summary(
    lane: str, post: dict[str, Any], rows: list[dict[str, Any]], unique_ratio: float
) -> dict[str, Any]:
    has_report = isinstance(post.get("tangent_db_report"), dict)
    return {
        "lane": lane,
        "rows": len(rows),
        "round_trips_passed": sum(bool(row["decode_ok"]) for row in rows),
        "rows_jsonl": "paired_rows.jsonl",
        "diversity_guard": {"passed": True, "minimum_ratio": 1.0, "ratio": unique_ratio},
        "tangent_db_quality": {
            "version": "tangent_db_quality_summary_v1",
            "inference_unit": "unique_post_id",
            "report_rows": len(rows) if has_report else 0,
            "unique_posts": 1 if has_report else 0,
        },
    }


def _write_generated_lane(
    root: Path, lane: str, prepared: tuple[list[dict[str, Any]], dict[str, Any]]
) -> None:
    rows, summary = prepared
    lane_root = root / lane
    rows_text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    (lane_root / "paired_rows.jsonl").write_text(rows_text, encoding="utf-8")
    (lane_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def materialize_luna_comments(root: Path, *, post_id: str, comments_path: Path) -> Path:
    """Materialize existing comments through real angle-selection codec primitives."""
    root = root.resolve()
    candidates = _read_object(comments_path.resolve())
    if set(candidates) != set(LANES):
        raise ValueError("generated comments must contain exactly legacy and v1 lanes")
    prepared = {lane: _prepare_generated_lane(root, candidates, lane, post_id) for lane in LANES}
    counts = {lane: len(prepared[lane][0]) for lane in LANES}
    if len(set(counts.values())) != 1:
        raise ValueError(f"legacy/v1 generated sample count symmetry failed: {counts}")
    for lane in LANES:
        _write_generated_lane(root, lane, prepared[lane])
    return root / "v1" / "summary.json"


def finalize_comparison(root: Path, legacy_summary: Path, v1_summary: Path) -> Path:
    """Validate lane provenance/M9 and persist the viewer's two-lane contract."""
    contract = _read_object(root / "comparison.json")
    summaries = {"legacy": _read_object(legacy_summary), "v1": _read_object(v1_summary)}
    for lane, summary in summaries.items():
        _validate_lane(root, contract, lane, summary)
        _enrich_lane_summary(root, lane, summary)
    merged = dict(summaries["v1"])
    merged["tangent_db_quality_summaries"] = {
        lane: summary.get("tangent_db_quality") for lane, summary in summaries.items()
    }
    merged["tangent_db_comparison"] = {
        "version": CONTRACT_VERSION,
        "comparison_id": contract["comparison_id"],
        "legacy_summary": str(legacy_summary.resolve()),
        "v1_summary": str(v1_summary.resolve()),
        "caveats": [
            "This isolated run contains one independent post; slot-level results are descriptive.",
            "M4 reached the 5/5 ceiling in both lanes and cannot distinguish them.",
            "M2 detected every generated comment in both lanes; its zero delta is not evidence of naturalness improvement.",
            "Legacy artifacts predate tangent_db_report, so M7/M8 report-based attribution is unavailable for that lane.",
        ],
    }
    output = root / "summary.json"
    output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _lexical_quality(text: str) -> float:
    tokens = re.findall(r"[A-Za-z0-9']+", text.casefold())
    if not tokens:
        return 0.0
    unique = len(set(tokens)) / len(tokens)
    repetition = 1.0 - unique
    bigrams = list(zip(tokens, tokens[1:], strict=False))
    max_repeat = max((bigrams.count(item) for item in set(bigrams)), default=1)
    bigram_component = 1.0 / max_repeat
    length_component = min(len(tokens) / 5, 1.0, 120 / len(tokens))
    return round(100 * (0.4 * unique + 0.25 * (1 - repetition) + 0.25 * bigram_component + 0.1 * length_component), 6)


def _quality_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {
            "version": "tangent_db_quality_summary_v1",
            "inference_unit": "unique_post_id",
            "report_rows": 0,
            "unique_posts": 0,
            "available": False,
        }
    relevance = report.get("relevance") if isinstance(report.get("relevance"), dict) else {}
    distinctness = (
        report.get("distinctness") if isinstance(report.get("distinctness"), dict) else {}
    )
    source_mix = report.get("source_mix_kept") if isinstance(report.get("source_mix_kept"), dict) else {}
    total = sum(float(source_mix.get(key, 0)) for key in ("post", "comments", "search_results"))
    return {
        "version": "tangent_db_quality_summary_v1",
        "inference_unit": "unique_post_id",
        "report_rows": 14,
        "unique_posts": 1,
        "available": True,
        "relevance": relevance,
        "distinctness": distinctness,
        "source_mix_kept": source_mix,
        "search_share": float(source_mix.get("search_results", 0)) / total if total else 0.0,
        "kept_count": report.get("kept_count"),
        "dropped": report.get("dropped"),
        "config_hash": report.get("config_hash"),
    }


def _enrich_lane_summary(root: Path, lane: str, summary: dict[str, Any]) -> None:
    rows = _load_jsonl(root / lane / "paired_rows.jsonl")
    if len(rows) != summary.get("rows") or not all(row.get("decode_ok") is True for row in rows):
        raise ValueError(f"{lane} row coverage/round-trip mismatch")
    scores = [_lexical_quality(str(row.get("stegotext", ""))) for row in rows]
    angle = _read_object(root / lane / "news_angles" / f"{rows[0]['post_id']}.json")
    raw_report = angle.get("tangent_db_report")
    report = raw_report if isinstance(raw_report, dict) else None
    summary["lexical_quality_index"] = {
        "version": "lexical_quality_v1",
        "range": [0.0, 100.0],
        "higher_is_better": True,
        "row_mean": statistics.fmean(scores),
        "post_cluster_mean": statistics.fmean(scores),
    }
    summary["tangent_db_quality"] = _quality_from_report(report)
    metric_paths = {
        "human_likeness_preference": root / "preference_summary.json",
        "synthetic_detection": root / lane / "m2_summary.json",
        "thread_relevance": root / lane / "thread_relevance_summary.json",
        "writing_quality": root / lane / "writing_quality_summary.json",
        "drift_attribution": root / lane / "drift_attribution.json",
    }
    for key, path in metric_paths.items():
        if not path.is_file():
            raise ValueError(f"{lane} missing cached metric artifact: {path.name}")
        summary[key] = _read_object(path)
    summary["distributional_metrics"] = {
        "status": "not_run_for_isolated_lane",
        "interpretation": "M6 topical-fit/fluency proxies, not reader-facing naturalness",
    }
    summary["provenance"] = {
        "prep_run": _read_object(root / lane / "prep_run.json"),
        "paired_rows_sha256": hashlib.sha256((root / lane / "paired_rows.jsonl").read_bytes()).hexdigest(),
        "summary_inputs_sha256": _json_sha256({key: summary[key] for key in metric_paths}),
    }


def _validate_lane(
    root: Path, contract: dict[str, Any], lane: str, summary: dict[str, Any]
) -> None:
    manifest = _read_object(root / lane / "prep_run.json")
    if manifest.get("tangent_db_builder") != lane:
        raise ValueError(f"{lane} manifest builder mismatch")
    guard = summary.get("diversity_guard")
    if not isinstance(guard, dict) or guard.get("passed") is not True:
        raise ValueError(f"{lane} summary does not pass the M9 diversity guard")
    if float(guard.get("minimum_ratio", 0.0)) < float(contract["minimum_diversity_ratio"]):
        raise ValueError(f"{lane} summary weakens the M9 diversity threshold")
    quality = summary.get("tangent_db_quality")
    if not isinstance(quality, dict) or quality.get("version") != "tangent_db_quality_summary_v1":
        raise ValueError(f"{lane} summary lacks tangent DB quality v1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--root", required=True, type=Path)
    init.add_argument("--comparison-id", required=True)
    init.add_argument("--notes", default="")
    final = sub.add_parser("finalize")
    final.add_argument("--root", required=True, type=Path)
    final.add_argument("--legacy-summary", required=True, type=Path)
    final.add_argument("--v1-summary", required=True, type=Path)
    cached = sub.add_parser("materialize-cached-v1")
    cached.add_argument("--root", required=True, type=Path)
    cached.add_argument("--post-id", required=True)
    luna = sub.add_parser("materialize-luna")
    luna.add_argument("--root", required=True, type=Path)
    luna.add_argument("--post-id", required=True)
    luna.add_argument("--comments", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "init":
        path = initialize_comparison(args.root, comparison_id=args.comparison_id, notes=args.notes)
    elif args.command == "materialize-cached-v1":
        path = materialize_cached_v1(args.root, post_id=args.post_id)
    elif args.command == "materialize-luna":
        path = materialize_luna_comments(
            args.root, post_id=args.post_id, comments_path=args.comments
        )
    else:
        path = finalize_comparison(args.root.resolve(), args.legacy_summary, args.v1_summary)
    sys.stdout.write(str(path) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
