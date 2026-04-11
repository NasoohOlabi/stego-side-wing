"""Centralized configuration for workflows."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional

from infrastructure.config import (
    REPO_ROOT,
    get_env,
    get_google_generative_language_api_key,
    get_step_dirs as get_global_step_dirs,
    resolve_path,
)


class WorkflowConfig:
    """Centralized workflow configuration."""

    def __init__(
        self,
        *,
        url_cache_dir: Optional[Path] = None,
        research_terms_db_path: Optional[Path] = None,
        angles_cache_dir: Optional[Path] = None,
    ) -> None:
        self.base_url = get_env("BASE_URL", "http://127.0.0.1:5001")
        self.base_url_lap = get_env("BASE_URL_LAP", "http://127.0.0.1:5000")
        self.model = get_env("MODEL", "mistral-nemo-instruct-2407-abliterated")

        # LLM API keys
        self.openai_api_key = get_env("OPENAI_API_KEY")
        self.google_palm_api_key = get_google_generative_language_api_key()
        self.groq_api_key = get_env("GROQ_API_KEY")

        # Dataset directories (single-source from infrastructure.config/STEPS)
        self.posts_directory, self.url_fetched_dir = get_global_step_dirs(
            "filter-url-unresolved"
        )
        _, self.researched_dir = get_global_step_dirs("filter-researched")
        _, self.angles_dir = get_global_step_dirs("angles-step")
        _, self.output_results_dir = get_global_step_dirs("final-step")
        self.url_cache_dir = (url_cache_dir or resolve_path("./datasets/url_cache")).resolve()
        self.research_terms_db_path = (
            research_terms_db_path
            if research_terms_db_path is not None
            else (self.posts_directory.parent / "research_terms_cache.db")
        ).resolve()
        self.angles_cache_dir = (
            angles_cache_dir if angles_cache_dir is not None else (REPO_ROOT / "datasets" / "angles_cache")
        ).resolve()

        # Ensure directories exist
        for dir_path in [
            self.posts_directory,
            self.url_fetched_dir,
            self.researched_dir,
            self.angles_dir,
            self.output_results_dir,
            self.url_cache_dir,
            self.angles_cache_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)
        self.research_terms_db_path.parent.mkdir(parents=True, exist_ok=True)
    
    def get_step_dirs(self, step: str) -> tuple[Path, Path]:
        """Get source and destination directories for a workflow step."""
        return get_global_step_dirs(step)


# Global config instance
_config: Optional[WorkflowConfig] = None

_workflow_config_ctx: ContextVar[Optional[WorkflowConfig]] = ContextVar(
    "workflow_config_override", default=None
)


def get_config() -> WorkflowConfig:
    """Return override config when inside :func:`isolated_workflow_config`, else the singleton."""
    ctx = _workflow_config_ctx.get()
    if ctx is not None:
        return ctx
    global _config
    if _config is None:
        _config = WorkflowConfig()
    return _config


@contextmanager
def isolated_workflow_config(cfg: WorkflowConfig) -> Iterator[None]:
    """Bind :func:`get_config`, URL/terms paths, and angles disk cache for this block."""
    from workflows.cache_context import angles_cache_context

    token = _workflow_config_ctx.set(cfg)
    try:
        with angles_cache_context(cfg.angles_cache_dir):
            yield
    finally:
        _workflow_config_ctx.reset(token)
