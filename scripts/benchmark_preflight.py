"""Validate the immutable inputs required for a publication benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _payload_entry(post_id: str) -> dict[str, Any]:
    seed = int(hashlib.sha256(post_id.encode()).hexdigest()[:8], 16)
    payload = hashlib.sha256(f"{post_id}:{seed}".encode()).hexdigest()[:8]
    return {
        "post_id": post_id,
        "seed": seed,
        "payload_bits": 64,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def _artifact_hashes(post_ids: list[str], directory: Path | None) -> dict[str, str]:
    if directory is None:
        return {}
    paths = {post_id: directory / f"{post_id}.json" for post_id in post_ids}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing frozen benchmark artifacts: {missing[:3]}")
    return {post_id: _sha256(path) for post_id, path in paths.items()}


def build_manifest(
    post_ids: list[str],
    model_path: Path,
    protocol_path: Path,
    *,
    angles_dir: Path | None = None,
    dataset_dir: Path | None = None,
) -> dict[str, Any]:
    if not post_ids or len(post_ids) != len(set(post_ids)):
        raise ValueError("post_ids must be non-empty and unique")
    models = _read(model_path)
    protocol = _read(protocol_path)
    model_rows = models.get("models")
    if not isinstance(model_rows, list) or not model_rows:
        raise ValueError("benchmark model manifest has no models")
    required = [row for row in model_rows if isinstance(row, dict) and row.get("required")]
    if not required:
        raise ValueError("benchmark model manifest has no required models")
    def _display(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("branch", "--show-current"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "post_ids": post_ids,
        "post_ids_sha256": hashlib.sha256("\n".join(post_ids).encode()).hexdigest(),
        "model_manifest": _display(model_path),
        "model_manifest_sha256": _sha256(model_path),
        "protocol": _display(protocol_path),
        "protocol_sha256": _sha256(protocol_path),
        "required_model_ids": [row["id"] for row in required if isinstance(row.get("id"), str)],
        "payload_bits": protocol.get("payload_bits", []),
        "payload_assignments": [_payload_entry(post_id) for post_id in post_ids],
        "angle_artifact_sha256": _artifact_hashes(post_ids, angles_dir),
        "dataset_artifact_sha256": _artifact_hashes(post_ids, dataset_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-ids", required=True, help="Text file with one immutable post ID per line")
    parser.add_argument("--output", default="metrics/benchmark_manifest.json")
    parser.add_argument("--models", default="config/benchmark_models.json")
    parser.add_argument("--protocol", default="config/benchmark_protocol.json")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--angles-dir", default="")
    parser.add_argument("--dataset-dir", default="")
    args = parser.parse_args()
    post_path = (ROOT / args.post_ids).resolve()
    model_path = (ROOT / args.models).resolve()
    protocol_path = (ROOT / args.protocol).resolve()
    post_ids = [line.strip() for line in post_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    manifest = build_manifest(
        post_ids,
        model_path,
        protocol_path,
        angles_dir=(ROOT / args.angles_dir).resolve() if args.angles_dir else None,
        dataset_dir=(ROOT / args.dataset_dir).resolve() if args.dataset_dir else None,
    )
    if manifest["git_dirty"] and not args.allow_dirty:
        raise SystemExit("Benchmark preflight refused: working tree is dirty (use --allow-dirty for exploratory runs).")
    output = (ROOT / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
