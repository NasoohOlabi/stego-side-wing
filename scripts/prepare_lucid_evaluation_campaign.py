#!/usr/bin/env python3
"""Freeze the eligible fresh-LUCID corpus for the six-repeat evaluation campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "datasets/prep_runs/LUCID/tangents_db_v1_fresh"
DEFAULT_CAMPAIGN = ROOT / "metrics/evaluation_campaigns/lucid_fresh_6x_20260815"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def build_manifest(source_root: Path, *, repeats: int, batch_posts: int) -> dict[str, Any]:
    fetched = source_root / "news_url_fetched"
    researched = source_root / "news_researched"
    angles = source_root / "news_angles"
    eligible: list[dict[str, str]] = []
    excluded: dict[str, int] = {}

    def reject(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for angle_path in sorted(angles.glob("*.json")):
        post_id = angle_path.stem
        research_path = researched / f"{post_id}.json"
        fetched_path = fetched / f"{post_id}.json"
        try:
            angle = _read_object(angle_path)
        except (OSError, ValueError, json.JSONDecodeError):
            reject("angle_unreadable")
            continue
        artifact = angle.get("angle_artifact") or {}
        if (
            len(angle.get("angles") or []) != 32
            or artifact.get("tangent_count") != 32
            or artifact.get("angles_target_reached") is not True
        ):
            reject("angles_incomplete")
            continue
        if not research_path.is_file():
            reject("research_missing")
            continue
        try:
            research = _read_object(research_path)
        except (OSError, ValueError, json.JSONDecodeError):
            reject("research_unreadable")
            continue
        if not (research.get("search_results") or []):
            reject("research_empty")
            continue
        if not fetched_path.is_file():
            reject("fetched_missing")
            continue
        eligible.append(
            {
                "post_id": post_id,
                "angle_sha256": _sha256(angle_path),
                "research_sha256": _sha256(research_path),
                "fetched_sha256": _sha256(fetched_path),
            }
        )

    if not eligible:
        raise ValueError("No eligible posts found")
    post_ids = [row["post_id"] for row in eligible]
    return {
        "schema_version": 1,
        "campaign": "lucid_fresh_six_embeddings_full_evaluation",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_root": str(source_root.resolve()),
        "source_dirs": {
            "angles": str(angles.resolve()),
            "researched": str(researched.resolve()),
            "dataset": str(fetched.resolve()),
        },
        "repeats_per_post": repeats,
        "batch_posts": batch_posts,
        "eligible_posts": len(eligible),
        "planned_embeddings": len(eligible) * repeats,
        "pilot_posts": min(25, len(eligible)),
        "excluded": excluded,
        "post_ids_sha256": hashlib.sha256("\n".join(post_ids).encode()).hexdigest(),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("branch", "--show-current"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "artifacts": eligible,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE))
    parser.add_argument("--campaign-dir", default=str(DEFAULT_CAMPAIGN))
    parser.add_argument("--repeats", type=int, default=6, choices=range(5, 8))
    parser.add_argument("--batch-posts", type=int, default=25)
    args = parser.parse_args()
    campaign_dir = Path(args.campaign_dir).resolve()
    manifest_path = campaign_dir / "manifest.json"
    if manifest_path.exists():
        raise SystemExit(f"Refusing to replace frozen manifest: {manifest_path}")
    manifest = build_manifest(
        Path(args.source_root).resolve(), repeats=args.repeats, batch_posts=args.batch_posts
    )
    campaign_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (campaign_dir / "post_ids.txt").write_text(
        "\n".join(row["post_id"] for row in manifest["artifacts"]) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: manifest[k] for k in (
        "eligible_posts", "planned_embeddings", "pilot_posts", "excluded"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
