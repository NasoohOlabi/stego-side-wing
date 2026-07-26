"""Per-post angle determinism evaluation used by ``WorkflowRunner``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from content_acquisition.angles import angle_runner
from workflows.runner_orchestration_utils import normalized_angles_from_raw
from workflows.utils.protocol_utils import stable_hash


def _dictionary_bundle(gen_angles: Any, post: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    if hasattr(gen_angles, "build_dictionary_bundle_for_post"):
        bundle = gen_angles.build_dictionary_bundle_for_post(post)
        return list(bundle["texts"]), dict(bundle["report"])
    dictionary = list(gen_angles.build_dictionary_for_post(post))
    return dictionary, {
        "dictionary_id": stable_hash(dictionary),
        "texts_hash": stable_hash(dictionary),
        "raw_entry_count": len(dictionary),
        "source_counts": {},
        "truncated_sources": [],
        "capacity_applied": False,
    }


def _row_base(
    stem: str,
    file_name: str,
    dictionary: list[str],
    report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "post_id": stem,
        "source_file": file_name,
        "input_text_blocks": len(dictionary),
        "input_hash": report["texts_hash"],
        "dictionary_id": report["dictionary_id"],
        "dictionary_raw_count": report["raw_entry_count"],
        "dictionary_source_counts": report["source_counts"],
        "dictionary_truncated_sources": report["truncated_sources"],
        "dictionary_capacity_applied": report["capacity_applied"],
    }


def evaluate_post(
    post_id_raw: str,
    *,
    step: str,
    backend: Any,
    gen_angles: Any,
    emit: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Load and evaluate one post with two uncached angle generations."""
    if not post_id_raw.strip():
        return {"post_id": post_id_raw, "error": "invalid post_id", "identical": None}
    stem = Path(post_id_raw.strip()).stem
    file_name = f"{stem}.json"
    emit(
        "stage_progress",
        {
            "stage": "batch-angles-determinism",
            "event": "post_start",
            "post_id": stem,
            "source_file": file_name,
        },
    )
    try:
        post = backend.get_post_local(file_name, step)
    except Exception as exc:
        row = {
            "post_id": stem,
            "source_file": file_name,
            "error": str(exc),
            "identical": None,
        }
        emit(
            "stage_progress",
            {
                "stage": "batch-angles-determinism",
                "event": "post_error",
                "post_id": stem,
                "error": str(exc),
            },
        )
        return row
    dictionary, report = _dictionary_bundle(gen_angles, post)
    base = _row_base(stem, file_name, dictionary, report)
    if not dictionary:
        row = {**base, "error": "no text blocks for angles input", "identical": None}
        emit("stage_progress", {"stage": "batch-angles-determinism", "event": "post_done", **row})
        return row
    try:
        raw_a = angle_runner.analyze_angles_from_texts(dictionary, use_cache=False)
        raw_b = angle_runner.analyze_angles_from_texts(dictionary, use_cache=False)
    except Exception as exc:
        row = {**base, "error": str(exc), "identical": None}
        emit(
            "stage_progress",
            {
                "stage": "batch-angles-determinism",
                "event": "post_error",
                "post_id": stem,
                "error": str(exc),
            },
        )
        return row
    norm_a = normalized_angles_from_raw(raw_a)
    norm_b = normalized_angles_from_raw(raw_b)
    h1, h2 = stable_hash(norm_a), stable_hash(norm_b)
    identical = norm_a == norm_b
    row = {
        **base,
        "run_1_count": len(norm_a),
        "run_2_count": len(norm_b),
        "run_1_hash": h1,
        "run_2_hash": h2,
        "identical": identical,
    }
    emit(
        "stage_progress",
        {
            "stage": "batch-angles-determinism",
            "event": "post_done",
            "post_id": stem,
            "identical": identical,
            "run_1_hash": h1,
            "run_2_hash": h2,
        },
    )
    return row
