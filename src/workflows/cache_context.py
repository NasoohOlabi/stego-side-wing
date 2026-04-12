"""Per-request / per-task overrides for workflow disk caches (angles LLM cache)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional

from infrastructure.config import REPO_ROOT

DEFAULT_ANGLES_CACHE_DIR = REPO_ROOT / "datasets" / "angles_cache"

_angles_cache_dir_var: ContextVar[Optional[Path]] = ContextVar("angles_cache_dir", default=None)


def get_angles_cache_dir() -> Path:
    """Angles on-disk cache directory for the current context (default: repo datasets)."""
    p = _angles_cache_dir_var.get()
    return p if p is not None else DEFAULT_ANGLES_CACHE_DIR


@contextmanager
def angles_cache_context(cache_dir: Path) -> Iterator[None]:
    """Bind angles disk cache (LM + ``workflow_google/`` workflow-LLM subtree) to ``cache_dir``."""
    token = _angles_cache_dir_var.set(cache_dir.resolve())
    try:
        yield
    finally:
        _angles_cache_dir_var.reset(token)
