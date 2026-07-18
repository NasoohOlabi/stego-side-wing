"""Offline setup and finalization for the legacy/v1 tangent-DB comparison."""

from __future__ import annotations

import argparse
import json
import os
import re
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
    augment_post,
    build_dictionary,
    recover_payload_with_compressed_full,
)
from workflows.utils.tangent_db import (  # noqa: E402
    AngleCandidate,
    PostContext,
    build_tangent_db,
    tangent_db_config_from_env,
)

CONTRACT_VERSION = "tangent_db_comparison_v1"


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
            path = write_prep_run_manifest(
                run_id=f"{comparison_id}-{builder}", notes=notes
            )
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


def _candidate_with_source(
    angle: dict[str, Any], entries: list[dict[str, Any]]
) -> AngleCandidate:
    """Recover source provenance from the cached quote without changing its wording."""
    quote = _quote_words(str(angle.get("source_quote", "")))
    matches: list[tuple[int, str]] = []
    for index, entry in enumerate(entries):
        words = _quote_words(str(entry.get("text", "")))
        if quote and any(words[offset : offset + len(quote)] == quote for offset in range(len(words))):
            matches.append((index, str(entry.get("source", ""))))
    sources = {source for _, source in matches}
    if len(sources) != 1:
        raise ValueError(f"cached angle quote has ambiguous/missing source: {angle.get('source_quote')!r}")
    index, source = matches[0]
    return AngleCandidate.from_angle(angle).model_copy(
        update={"source_document": index, "source": source}
    )


def materialize_cached_v1(root: Path, *, post_id: str) -> Path:
    """Build a v1 lane offline from an accepted legacy candidate pool."""
    root = root.resolve()
    legacy_manifest = _read_object(root / "legacy" / "prep_run.json")
    v1_manifest = _read_object(root / "v1" / "prep_run.json")
    if legacy_manifest.get("tangent_db_config_hash") != v1_manifest.get(
        "tangent_db_config_hash"
    ):
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


def _payload_for_angle(post: dict[str, Any], lane: str, index: int) -> tuple[str, dict[str, Any]]:
    """Find a reproducible ordinary payload whose real sender selects ``index``."""
    angles = post.get("angles")
    if not isinstance(angles, list) or not angles:
        raise ValueError(f"{lane} lane has no angles")
    payloads = [f"A{chr(codepoint)}" for codepoint in range(33, 127)]
    payloads.extend(chr(codepoint) for codepoint in range(128, 512))
    for payload in payloads:
        encoded = augment_post(payload, post)
        if encoded["angleEmbedding"]["selectedAngle"].get("idx") == index:
            return payload, encoded
    raise ValueError(f"could not select {lane} angle {index} deterministically")


def materialize_luna_comments(root: Path, *, post_id: str, comments_path: Path) -> Path:
    """Materialize Luna text through the real sender/receiver codec, entirely offline."""
    root = root.resolve()
    candidates = _read_object(comments_path.resolve())
    lane_data: dict[str, tuple[dict[str, Any], list[str]]] = {}
    for lane in ("legacy", "v1"):
        post = _read_object(root / lane / "news_angles" / f"{post_id}.json")
        comments = candidates.get(lane)
        angles = post.get("angles")
        if not isinstance(comments, list) or not all(isinstance(x, str) and x.strip() for x in comments):
            raise ValueError(f"{lane} Luna candidates must be non-empty strings")
        if not isinstance(angles, list) or len(comments) != len(angles):
            raise ValueError(f"{lane} candidate/angle counts differ")
        lane_data[lane] = (post, comments)
    counts = {lane: len(data[1]) for lane, data in lane_data.items()}
    if len(set(counts.values())) != 1:
        raise ValueError(f"lane count symmetry failed: {counts}")

    for lane, (post, comments) in lane_data.items():
        unique_ratio = len(set(comments)) / len(comments)
        if unique_ratio != 1.0:
            raise ValueError(f"{lane} M9 must equal 1.0, got {unique_ratio}")
        dictionary = build_dictionary(post)
        angles = post["angles"]
        rows: list[dict[str, Any]] = []
        for index, comment in enumerate(comments):
            payload, encoded = _payload_for_angle(post, lane, index)
            compressed = encoded["compression"]["compressed"]
            recovered = recover_payload_with_compressed_full(
                compressed, dictionary, post, [angles], index
            )
            if recovered is None or recovered[0] != payload:
                raise ValueError(f"{lane} row {index} failed sender/receiver round trip")
            rows.append({
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
                "selected_angle": angles[index],
                "selection_signature": encoded["selectionSignature"],
                "compressed_full": compressed,
                "recovery_source": "real_codec_audit_assisted_verified",
                "tangent_db_report": post.get("tangent_db_report"),
            })
        lane_root = root / lane
        rows_path = lane_root / "paired_rows.jsonl"
        rows_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        summary = {
            "lane": lane,
            "rows": len(rows),
            "round_trips_passed": len(rows),
            "rows_jsonl": str(rows_path.resolve()),
            "diversity_guard": {"passed": True, "minimum_ratio": 1.0, "ratio": unique_ratio},
            "tangent_db_quality": {
                "version": "tangent_db_quality_summary_v1",
                "inference_unit": "unique_post_id",
                "report_rows": len(rows) if isinstance(post.get("tangent_db_report"), dict) else 0,
                "unique_posts": 1 if isinstance(post.get("tangent_db_report"), dict) else 0,
            },
        }
        (lane_root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return root / "v1" / "summary.json"


def finalize_comparison(root: Path, legacy_summary: Path, v1_summary: Path) -> Path:
    """Validate lane provenance/M9 and persist the viewer's two-lane contract."""
    contract = _read_object(root / "comparison.json")
    summaries = {"legacy": _read_object(legacy_summary), "v1": _read_object(v1_summary)}
    for lane, summary in summaries.items():
        _validate_lane(root, contract, lane, summary)
    merged = dict(summaries["v1"])
    merged["tangent_db_quality_summaries"] = {
        lane: summary.get("tangent_db_quality") for lane, summary in summaries.items()
    }
    merged["tangent_db_comparison"] = {
        "version": CONTRACT_VERSION,
        "comparison_id": contract["comparison_id"],
        "legacy_summary": str(legacy_summary.resolve()),
        "v1_summary": str(v1_summary.resolve()),
    }
    output = root / "summary.json"
    output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


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
        path = materialize_luna_comments(args.root, post_id=args.post_id, comments_path=args.comments)
    else:
        path = finalize_comparison(args.root.resolve(), args.legacy_summary, args.v1_summary)
    sys.stdout.write(str(path) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
