"""List persisted double-process validation reports from disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field, validate_call

from infrastructure.json_logging import get_trace_id
from workflows.runner_orchestration_utils import double_process_cache_base_root

_LOG = logger.bind(component="DoubleProcessHistory")

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100


class DoubleProcessRunEntry(BaseModel):
    """One report file under ``DOUBLE_PROCESS_VALIDATION_ROOT/reports/``."""

    model_config = {"extra": "forbid"}

    report_path: str
    file_mtime: float
    record: dict[str, Any]


class DoubleProcessHistoryResult(BaseModel):
    """Result of listing double-process reports."""

    model_config = {"extra": "forbid"}

    runs: list[DoubleProcessRunEntry]
    count: int
    base_path: str


def _trace() -> str:
    return get_trace_id() or str(uuid4())


def _reports_dir(base: Path) -> Path:
    return base / "reports"


def _sorted_json_paths(reports_dir: Path) -> list[Path]:
    if not reports_dir.is_dir():
        return []
    paths = sorted(
        reports_dir.glob("*.json"),
        key=lambda p: (-p.stat().st_mtime, p.name),
    )
    return paths


def _parse_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _LOG.bind(trace_id=_trace(), report_path=str(path)).exception(
            "double_process_history_parse_failed",
        )
        return None
    if not isinstance(raw, dict):
        _LOG.bind(trace_id=_trace(), report_path=str(path)).error(
            "double_process_history_invalid_shape",
        )
        return None
    return raw


def _matches_post(record: dict[str, Any], post_id: str | None) -> bool:
    if post_id is None:
        return True
    rid = record.get("post_id")
    return isinstance(rid, str) and rid == post_id


def _collect_run_entries(
    base: Path, post_id: str | None, limit: int
) -> list[DoubleProcessRunEntry]:
    out: list[DoubleProcessRunEntry] = []
    for path in _sorted_json_paths(_reports_dir(base)):
        rec = _parse_record(path)
        if rec is None or not _matches_post(rec, post_id):
            continue
        st = path.stat()
        out.append(
            DoubleProcessRunEntry(
                report_path=str(path.resolve()),
                file_mtime=st.st_mtime,
                record=rec,
            )
        )
        if len(out) >= limit:
            break
    return out


@validate_call
def list_double_process_runs(
    *,
    base_path: Path | None = None,
    post_id: str | None = None,
    limit: Annotated[int, Field(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
) -> DoubleProcessHistoryResult:
    """Load persisted double-process JSON reports, newest first."""
    base = base_path.resolve() if base_path is not None else double_process_cache_base_root()
    runs = _collect_run_entries(base, post_id, limit)
    return DoubleProcessHistoryResult(
        runs=runs,
        count=len(runs),
        base_path=str(base.resolve()),
    )
