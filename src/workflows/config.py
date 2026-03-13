"""Centralized configuration for workflows."""
from pathlib import Path
from typing import Optional

from infrastructure.config import (
    REPO_ROOT,
    get_env,
    get_step_dirs as get_global_step_dirs,
    resolve_path,
)


class WorkflowConfig:
    """Centralized workflow configuration."""
    
    def __init__(self):
        self.base_url = get_env("BASE_URL", "http://127.0.0.1:5001")
        self.base_url_lap = get_env("BASE_URL_LAP", "http://127.0.0.1:5000")
        self.model = get_env("MODEL", "mistral-nemo-instruct-2407-abliterated")
        
        # LLM API keys
        self.openai_api_key = get_env("OPENAI_API_KEY")
        self.google_palm_api_key = get_env("GOOGLE_PALM_API_KEY")
        self.groq_api_key = get_env("GROQ_API_KEY")

        # Dataset directories (single-source from infrastructure.config/STEPS)
        self.posts_directory, self.url_fetched_dir = get_global_step_dirs(
            "filter-url-unresolved"
        )
        _, self.researched_dir = get_global_step_dirs("filter-researched")
        _, self.angles_dir = get_global_step_dirs("angles-step")
        _, self.output_results_dir = get_global_step_dirs("final-step")
        self.url_cache_dir = resolve_path("./datasets/url_cache")
        
        # Ensure directories exist
        for dir_path in [
            self.posts_directory,
            self.url_fetched_dir,
            self.researched_dir,
            self.angles_dir,
            self.output_results_dir,
            self.url_cache_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)
    
    def get_step_dirs(self, step: str) -> tuple[Path, Path]:
        """Get source and destination directories for a workflow step."""
        return get_global_step_dirs(step)


# Global config instance
_config: Optional[WorkflowConfig] = None


def get_config() -> WorkflowConfig:
    """Get the global workflow config instance."""
    global _config
    if _config is None:
        _config = WorkflowConfig()
    return _config
