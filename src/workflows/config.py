"""Centralized configuration for workflows."""
import os
from pathlib import Path
from typing import Optional

import dotenv

dotenv.load_dotenv()


class WorkflowConfig:
    """Centralized workflow configuration."""
    
    def __init__(self):
        self.base_url = self._get_env("BASE_URL", "http://127.0.0.1:5001")
        self.base_url_lap = self._get_env("BASE_URL_LAP", "http://127.0.0.1:5000")
        self.model = self._get_env("MODEL", "mistral-nemo-instruct-2407-abliterated")
        
        # LLM API keys
        self.openai_api_key = self._get_env("OPENAI_API_KEY")
        self.google_palm_api_key = self._get_env("GOOGLE_PALM_API_KEY")
        self.groq_api_key = self._get_env("GROQ_API_KEY")
        
        # Dataset directories (matching API.py structure)
        repo_root = Path(__file__).resolve().parent.parent.parent
        self.posts_directory = repo_root / "datasets" / "news_cleaned"
        self.url_fetched_dir = repo_root / "datasets" / "news_url_fetched"
        self.researched_dir = repo_root / "datasets" / "news_researched"
        self.angles_dir = repo_root / "datasets" / "news_angles"
        self.output_results_dir = repo_root / "output-results"
        self.url_cache_dir = repo_root / "datasets" / "url_cache"
        
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
    
    @staticmethod
    def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
        """Get environment variable with fallback to .env file."""
        value = os.environ.get(key)
        if value:
            return value
        return dotenv.get_key(".env", key) or default
    
    def get_step_dirs(self, step: str) -> tuple[Path, Path]:
        """Get source and destination directories for a workflow step."""
        step_map = {
            "filter-url-unresolved": (self.posts_directory, self.url_fetched_dir),
            "filter-researched": (self.url_fetched_dir, self.researched_dir),
            "angles-step": (self.researched_dir, self.angles_dir),
            "final-step": (self.angles_dir, self.output_results_dir),
        }
        if step not in step_map:
            raise ValueError(f"Unknown step: {step}")
        return step_map[step]


# Global config instance
_config: Optional[WorkflowConfig] = None


def get_config() -> WorkflowConfig:
    """Get the global workflow config instance."""
    global _config
    if _config is None:
        _config = WorkflowConfig()
    return _config
