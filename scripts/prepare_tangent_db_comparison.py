"""Offline setup and finalization for the legacy/v1 tangent-DB comparison."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.prep_run_manifest import write_prep_run_manifest  # noqa: E402

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
    args = parser.parse_args()
    path = (
        initialize_comparison(args.root, comparison_id=args.comparison_id, notes=args.notes)
        if args.command == "init"
        else finalize_comparison(args.root.resolve(), args.legacy_summary, args.v1_summary)
    )
    sys.stdout.write(str(path) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
