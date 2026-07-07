"""Run cached/snapshot and no-cache/volatile receiver checks for saved posts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from workflows.pipelines.receiver import ReceiverPipeline  # noqa: E402


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _read_post(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _run_one(receiver: ReceiverPipeline, post: dict[str, Any], sender: str, *, volatile: bool) -> dict[str, Any]:
    return receiver.run(
        post,
        sender,
        use_fetch_cache=not volatile,
        use_terms_cache=not volatile,
        persist_terms_cache=not volatile,
        use_fetch_cache_research=not volatile,
        fail_on_context_drift=False,
    )


def run_receiver_volatility_experiment(
    *,
    post_files: list[Path],
    sender_user_id: str,
    run_dir: Path,
) -> dict[str, Any]:
    receiver = ReceiverPipeline()
    lanes: list[dict[str, Any]] = []
    for mode, volatile in (("snapshot_receiver", False), ("volatile_receiver", True)):
        entries = []
        failures = []
        for sample_index, post_file in enumerate(post_files):
            try:
                post = _read_post(post_file)
                out = _run_one(receiver, post, sender_user_id, volatile=volatile)
                entry = {
                    "post_id": str(post.get("id") or post_file.stem),
                    "sample_index": sample_index,
                    "receiver_decode": {
                        "decoded_angle_index": out.get("decoded_angle_index"),
                        "recovery_meta": out.get("recovery_meta", {}),
                    },
                    "sample_metrics": {"receiver_success": bool(out.get("succeeded"))},
                    "context_drift": out.get("context_drift"),
                }
                if out.get("context_drift", {}).get("mismatches"):
                    entry["context_drift_failure"] = True
                entries.append(entry)
            except Exception as exc:
                failures.append(
                    {
                        "post_id": post_file.stem,
                        "sample_index": sample_index,
                        "failure_code": "receiver_failure",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        lanes.append(
            {
                "profile": mode,
                "variant": mode,
                "requested_samples": len(post_files),
                "samples_succeeded": len(entries),
                "samples_failed": len(failures),
                "entries": entries,
                "failures": failures,
            }
        )
    summary = {
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_dir": str(run_dir.resolve()),
        "profile_summaries": lanes,
    }
    _write_json(run_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare snapshot and volatile receiver behavior.")
    parser.add_argument("--post-file", action="append", required=True)
    parser.add_argument("--sender-user-id", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_receiver_volatility_experiment(
        post_files=[Path(value) for value in args.post_file],
        sender_user_id=args.sender_user_id,
        run_dir=Path(args.run_dir),
    )


if __name__ == "__main__":
    main()
