"""Run one post twice with fresh sender/receiver caches for each pass."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from services.workflow_run_tracker import get_run_id  # noqa: E402
from workflows.adapters.backend_api import BackendAPIAdapter  # noqa: E402
from workflows.runner_orchestration_utils import run_stego_receiver_live_sim_once  # noqa: E402
from workflows.utils.protocol_utils import stable_hash  # noqa: E402

RUNS_ROOT = _REPO_ROOT / "metrics" / "single_post_twice_no_cache_runs"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_post(post_id: str | None, dataset_dir: Path) -> str:
    if isinstance(post_id, str) and post_id.strip():
        return Path(post_id.strip()).stem
    backend = BackendAPIAdapter()
    posts = backend.posts_list(step="final-step", count=1, offset=0)
    file_names = posts.get("fileNames", [])
    if not file_names:
        raise ValueError("No post available in final-step")
    return Path(str(file_names[0])).stem


def _run_once(*, base: Path, post_id: str, payload: str | None, tag: str | None) -> dict[str, Any]:
    return run_stego_receiver_live_sim_once(
        uid="sender-user",
        post_id=post_id,
        stego_list_offset=0,
        payload=payload,
        tag=tag,
        base=base,
        attempt_idx=1,
        multi_post=False,
        allow_fallback=False,
        compressed_full=None,
        max_padding_bits=256,
        on_progress=None,
    )


def run_single_post_twice_no_cache(
    *,
    post_id: str | None,
    payload: str | None,
    tag: str | None,
    dataset_dir: Path,
    run_dir: Path | None,
    overwrite: bool,
) -> dict[str, Any]:
    resolved_post_id = _resolve_post(post_id, dataset_dir)
    resolved_run_dir = (
        run_dir or RUNS_ROOT / f"single_post_{resolved_post_id}_{uuid4().hex}"
    ).resolve()
    if resolved_run_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Run directory already exists: {resolved_run_dir}")
        shutil.rmtree(resolved_run_dir)
    resolved_run_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for idx in (1, 2):
        pass_root = resolved_run_dir / f"pass_{idx}"
        if pass_root.exists():
            shutil.rmtree(pass_root)
        pass_root.mkdir(parents=True, exist_ok=True)
        outcome = _run_once(base=pass_root, post_id=resolved_post_id, payload=payload, tag=tag)
        results.append(outcome)
        shutil.rmtree(pass_root, ignore_errors=True)

    first = results[0]
    second = results[1]
    summary = {
        "run_id": str(get_run_id() or ""),
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_dir": str(resolved_run_dir),
        "post_id": resolved_post_id,
        "payload_hash": stable_hash(payload or ""),
        "tag": tag,
        "pass_1": first,
        "pass_2": second,
        "same_succeeded": bool(first.get("succeeded")) == bool(second.get("succeeded")),
        "same_stego_hash": stable_hash(str(first.get("stego", {}).get("stego_text", "")))
        == stable_hash(str(second.get("stego", {}).get("stego_text", ""))),
        "same_receiver_hash": stable_hash(first.get("receiver", {}))
        == stable_hash(second.get("receiver", {})),
    }
    _write_json(resolved_run_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a single post twice with fresh sender/receiver caches each time."
    )
    parser.add_argument("--post-id", default=None)
    parser.add_argument("--payload", default=None)
    parser.add_argument("--tag", default=None)
    parser.add_argument(
        "--dataset-dir",
        default=str(_REPO_ROOT / "datasets" / "news_cleaned"),
    )
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    result = run_single_post_twice_no_cache(
        post_id=args.post_id,
        payload=args.payload,
        tag=args.tag,
        dataset_dir=Path(args.dataset_dir),
        run_dir=Path(args.run_dir).resolve() if args.run_dir else None,
        overwrite=bool(args.overwrite),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    append_current_pid_to_log()
    main()
