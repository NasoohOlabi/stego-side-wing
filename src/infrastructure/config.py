"""Configuration management."""
import os
from pathlib import Path
from typing import Dict, Optional

import dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = REPO_ROOT / ".env"

# Load .env file once at module level.
dotenv.load_dotenv(dotenv_path=ENV_FILE_PATH if ENV_FILE_PATH.exists() else None)
_DOTENV_VALUES: Optional[Dict[str, Optional[str]]] = None


def _load_dotenv_values() -> Dict[str, Optional[str]]:
    """Load and cache .env key-values without printing missing-key warnings."""
    global _DOTENV_VALUES
    if _DOTENV_VALUES is None:
        _DOTENV_VALUES = (
            dotenv.dotenv_values(str(ENV_FILE_PATH)) if ENV_FILE_PATH.exists() else {}
        )
    return _DOTENV_VALUES


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get environment variable, checking both os.environ and .env file.
    
    Args:
        key: Environment variable name
        default: Default value if not found
        
    Returns:
        Environment variable value or default
    """
    value = os.environ.get(key)
    if value:
        return value

    value = _load_dotenv_values().get(key)
    return value if value else default


def get_env_required(key: str) -> str:
    """
    Get required environment variable, raising error if not found.
    
    Args:
        key: Environment variable name
        
    Returns:
        Environment variable value
        
    Raises:
        ValueError: If key is not found
    """
    value = get_env(key)
    if not value:
        raise ValueError(f"Required environment variable {key} not found")
    return value


def get_lm_studio_url(default: Optional[str] = None) -> str:
    """
    Get LM Studio base URL normalized to include /v1.

    This allows either a root tunnel URL (e.g. https://...trycloudflare.com/)
    or a full /v1 URL in the environment.
    """
    fallback = default or "https://approx-chocolate-earth-federation.trycloudflare.com/"
    raw_value = get_env("LM_STUDIO_URL", fallback) or fallback
    normalized = raw_value.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


# Common configuration constants
POSTS_DIRECTORY = "datasets/news_cleaned"

STEPS = {
    "filter-url-unresolved": {
        "source_dir": POSTS_DIRECTORY,
        "dest_dir": "./datasets/news_url_fetched",
    },
    "filter-researched": {
        "source_dir": "./datasets/news_url_fetched",
        "dest_dir": "./datasets/news_researched",
    },
    "angles-step": {
        "source_dir": "./datasets/news_researched",
        "dest_dir": "./datasets/news_angles",
    },
    "final-step": {
        "source_dir": "./datasets/news_angles",
        "dest_dir": "./output-results",
    },
}

def resolve_path(path_str: str) -> Path:
    """Resolve a project-relative path to absolute Path."""
    normalized = path_str[2:] if path_str.startswith("./") else path_str
    return REPO_ROOT / normalized


def get_step_dirs(step: str) -> tuple[Path, Path]:
    """Return absolute source/destination directories for a configured step."""
    if step not in STEPS:
        raise ValueError(f"Invalid step: {step}")
    source_dir = resolve_path(STEPS[step]["source_dir"])
    dest_dir = resolve_path(STEPS[step]["dest_dir"])
    return source_dir, dest_dir
