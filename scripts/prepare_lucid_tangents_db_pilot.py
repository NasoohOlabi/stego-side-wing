"""Offline scaffold for a Project LUCID TangentsDB-v1 frozen pilot.

Creates an isolated prep root with WORKFLOW_TANGENT_DB_BUILDER=lucid, writes a
prep-run manifest, and records the contract used for a small frozen e2e pilot.
Does not start provider calls unless ``--materialize-from`` is supplied with
already-researched posts (then uses extractive/local generation only).
"""

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

from workflows.pipelines.gen_angles import GenAnglesPipeline  # noqa: E402
from workflows.utils.prep_run_manifest import write_prep_run_manifest  # noqa: E402

CONTRACT_VERSION = "lucid_tangents_db_pilot_v1"
DEFAULT_ROOT = _REPO_ROOT / "datasets" / "prep_runs" / "LUCID" / "tangents_db_v1"


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def initialize_pilot(root: Path, *, pilot_id: str, notes: str = "") -> Path:
    """Create the lucid lane root and contract without provider work."""
    root = root.resolve()
    lane_root = root / "lucid"
    prior_root = os.environ.get("WORKFLOW_DATASET_ROOT")
    prior_builder = os.environ.get("WORKFLOW_TANGENT_DB_BUILDER")
    try:
        os.environ["WORKFLOW_DATASET_ROOT"] = str(lane_root)
        os.environ["WORKFLOW_TANGENT_DB_BUILDER"] = "lucid"
        manifest = write_prep_run_manifest(run_id=f"{pilot_id}-lucid", notes=notes)
    finally:
        _restore_env("WORKFLOW_DATASET_ROOT", prior_root)
        _restore_env("WORKFLOW_TANGENT_DB_BUILDER", prior_builder)
    contract = {
        "version": CONTRACT_VERSION,
        "pilot_id": pilot_id,
        "builder": "lucid",
        "artifact_namespace": "project_lucid/tangents_db/v1",
        "lane_root": str(lane_root),
        "prep_manifest": str(manifest),
        "live_provider_calls_started": False,
        "e2e_hint": {
            "angles_dir": str(lane_root / "news_angles"),
            "env": {"WORKFLOW_TANGENT_DB_BUILDER": "lucid"},
            "note": (
                "Run a small frozen e2e only after angle artifacts exist under angles_dir. "
                "Do not rename LUCID_context_weighted_v2_balanced_500 as TangentsDB-v1."
            ),
        },
    }
    path = root / "pilot.json"
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return path


def materialize_from_researched(
    *,
    researched_dir: Path,
    output_angles_dir: Path,
    limit: int,
) -> dict[str, Any]:
    """Build LUCID angle artifacts from cached researched posts (no search calls)."""
    researched_dir = researched_dir.resolve()
    output_angles_dir = output_angles_dir.resolve()
    output_angles_dir.mkdir(parents=True, exist_ok=True)
    prior_builder = os.environ.get("WORKFLOW_TANGENT_DB_BUILDER")
    prior_mode = os.environ.get("WORKFLOW_ANGLES_GENERATION_MODE")
    os.environ["WORKFLOW_TANGENT_DB_BUILDER"] = "lucid"
    os.environ["WORKFLOW_ANGLES_GENERATION_MODE"] = "extractive_zero_kld"
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = None  # type: ignore[assignment]
    written: list[str] = []
    try:
        paths = sorted(researched_dir.glob("*.json"))[:limit]
        for path in paths:
            post = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(post, dict):
                continue
            out = pipeline.preview_post(post)
            post_id = str(out["post"].get("id") or path.stem)
            target = output_angles_dir / f"{post_id}_LUCID.json"
            target.write_text(json.dumps(out["post"], ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
            written.append(str(target))
    finally:
        _restore_env("WORKFLOW_TANGENT_DB_BUILDER", prior_builder)
        _restore_env("WORKFLOW_ANGLES_GENERATION_MODE", prior_mode)
    return {"written": len(written), "paths": written}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--pilot-id", default="lucid_tangents_db_v1_pilot")
    parser.add_argument("--notes", default="Project LUCID TangentsDB-v1 frozen pilot scaffold")
    parser.add_argument(
        "--materialize-from",
        default="",
        help="Optional researched-post directory to build extractive LUCID angle artifacts",
    )
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    root = Path(args.root)
    contract_path = initialize_pilot(root, pilot_id=args.pilot_id, notes=args.notes)
    print(f"wrote {contract_path}")
    if args.materialize_from:
        angles_dir = root / "lucid" / "news_angles"
        summary = materialize_from_researched(
            researched_dir=Path(args.materialize_from),
            output_angles_dir=angles_dir,
            limit=max(1, args.limit),
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["live_provider_calls_started"] = False
        contract["materialized_angles"] = summary
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        print(f"materialized {summary['written']} angle artifacts under {angles_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
