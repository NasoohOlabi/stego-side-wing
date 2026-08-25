#!/usr/bin/env python3
"""Run the frozen LUCID corpus in resumable 25-post, six-repeat batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = ROOT / "metrics/evaluation_campaigns/lucid_fresh_6x_20260815"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(manifest: dict[str, Any]) -> None:
    dirs = manifest["source_dirs"]
    for row in manifest["artifacts"]:
        post_id = row["post_id"]
        for key, directory in (
            ("angle_sha256", dirs["angles"]),
            ("research_sha256", dirs["researched"]),
            ("fetched_sha256", dirs["dataset"]),
        ):
            path = Path(directory) / f"{post_id}.json"
            if not path.is_file() or _sha256(path) != row[key]:
                raise ValueError(f"Frozen input changed: {path}")


def _complete(run_dir: Path, requested: int) -> bool:
    summary = run_dir / "summary.json"
    if not summary.is_file():
        return False
    value = _read(summary)
    return int(value.get("total_requested_samples") or 0) == requested


def _combined_summary(campaign_dir: Path, batch_dirs: list[Path]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    requested = 0
    for run_dir in batch_dirs:
        summary_path = run_dir / "summary.json"
        if not summary_path.is_file():
            continue
        summary = _read(summary_path)
        requested += int(summary.get("total_requested_samples") or 0)
        for profile in summary.get("profile_summaries") or []:
            if isinstance(profile, dict):
                entries.extend(e for e in profile.get("entries") or [] if isinstance(e, dict))
                failures.extend(e for e in profile.get("failures") or [] if isinstance(e, dict))
    combined = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "campaign_dir": str(campaign_dir),
        "requested": requested,
        "succeeded": len(entries),
        "failed": len(failures),
        "entries": entries,
        "failures": failures,
        "batch_dirs": [str(path) for path in batch_dirs],
    }
    (campaign_dir / "combined_summary.json").write_text(
        json.dumps(combined, indent=2) + "\n", encoding="utf-8"
    )
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", default=str(DEFAULT_CAMPAIGN))
    parser.add_argument("--stage", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--max-retries", type=int, default=1)
    args = parser.parse_args()
    campaign_dir = Path(args.campaign_dir).resolve()
    manifest = _read(campaign_dir / "manifest.json")
    _verify(manifest)
    repeats = int(manifest["repeats_per_post"])
    batch_posts = int(manifest["batch_posts"])
    post_ids = [row["post_id"] for row in manifest["artifacts"]]
    if args.stage == "pilot":
        post_ids = post_ids[: int(manifest["pilot_posts"])]
    batches = [post_ids[i : i + batch_posts] for i in range(0, len(post_ids), batch_posts)]
    run_root = campaign_dir / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    batch_dirs: list[Path] = []
    env = os.environ.copy()
    env["WORKFLOW_LLM_BACKEND"] = "lm_studio"
    env.setdefault("LM_STUDIO_URL", "http://127.0.0.1:8081")

    for index, batch in enumerate(batches, start=1):
        run_dir = run_root / f"batch_{index:04d}"
        batch_dirs.append(run_dir)
        requested = len(batch) * repeats
        if _complete(run_dir, requested):
            print(f"skip complete {run_dir}", flush=True)
            continue
        if run_dir.exists():
            raise RuntimeError(
                f"Incomplete batch retained at {run_dir}; inspect it and move it aside before retrying"
            )
        command = [
            "uv", "run", "python", "scripts/run_actual_workload_e2e.py",
            "--variant", "balanced",
            "--samples-per-profile", str(requested),
            "--allow-post-reuse",
            "--context-sampler", "context_weighted_v2",
            "--angles-dir", manifest["source_dirs"]["angles"],
            "--dataset-dir", manifest["source_dirs"]["dataset"],
            "--run-dir", str(run_dir),
            "--max-retries", str(args.max_retries),
            "--progress-log", str(campaign_dir / "progress.jsonl"),
            "--log-level", "INFO",
        ]
        for post_id in batch:
            command.extend(("--post-id", post_id))
        print("+", " ".join(command), flush=True)
        subprocess.check_call(command, cwd=ROOT, env=env)
        combined = _combined_summary(campaign_dir, batch_dirs)
        print(json.dumps({k: combined[k] for k in ("requested", "succeeded", "failed")}), flush=True)

    combined = _combined_summary(campaign_dir, batch_dirs)
    print(json.dumps({k: combined[k] for k in ("requested", "succeeded", "failed")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
