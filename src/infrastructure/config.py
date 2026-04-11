"""Configuration management."""
import os
import re
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set, Tuple

import dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = REPO_ROOT / ".env"

# Load .env file once at module level.
dotenv.load_dotenv(dotenv_path=ENV_FILE_PATH if ENV_FILE_PATH.exists() else None)
_dotenv_values_cache: Optional[Dict[str, Optional[str]]] = None


def _load_dotenv_values() -> Dict[str, Optional[str]]:
    """Load and cache .env key-values without printing missing-key warnings."""
    global _dotenv_values_cache
    if _dotenv_values_cache is None:
        _dotenv_values_cache = (
            dotenv.dotenv_values(str(ENV_FILE_PATH)) if ENV_FILE_PATH.exists() else {}
        )
    return _dotenv_values_cache


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


def get_lm_studio_request_timeout_seconds(default: int = 600) -> int:
    """
    HTTP timeout (seconds) for LM Studio OpenAI-compatible /chat/completions.

    Single value applies to connect + read (``requests`` timeout).
    Override with ``LM_STUDIO_REQUEST_TIMEOUT_SEC`` (integer seconds).
    """
    raw = get_env("LM_STUDIO_REQUEST_TIMEOUT_SEC")
    if not raw:
        return default
    try:
        n = int(raw.strip())
    except ValueError:
        return default
    return max(30, min(n, 86400))


DEFAULT_GOOGLE_AI_STUDIO_MODEL = "gemma-4-26b-a4b-it"


def get_workflow_llm_backend() -> Literal["lm_studio", "google"]:
    """Workflow LLM target: Google AI Studio (Generative Language API) or local LM Studio."""
    raw = (get_env("WORKFLOW_LLM_BACKEND") or "ai_studio").strip().lower()
    if raw in ("google", "gemini", "ai_studio"):
        return "google"
    return "lm_studio"


def get_google_ai_studio_model() -> str:
    """Generative Language API model id when workflow LLM backend is AI Studio / Google."""
    explicit = (get_env("GOOGLE_AI_STUDIO_MODEL") or "").strip()
    return explicit or DEFAULT_GOOGLE_AI_STUDIO_MODEL


def _parse_api_key_list(raw: Optional[str]) -> list[str]:
    """Split comma- or whitespace-separated API key tokens."""
    if not raw:
        return []
    return [p for p in (s.strip() for s in re.split(r"[\s,]+", raw.strip())) if p]


def get_google_generative_language_api_keys() -> List[str]:
    """
    API keys for ``generativelanguage.googleapis.com`` (AI Studio / Gemini), in try order.

    Order: ``GOOGLE_PALM_API_KEY`` (if set), then ``GOOGLE_AI_API_KEYS``, then
    ``GOOGLE_AI_API_KEY`` (each of the latter may be comma-separated). Duplicates removed.
    """
    seen: Set[str] = set()
    out: List[str] = []
    for chunk in (
        _parse_api_key_list(get_env("GOOGLE_PALM_API_KEY"))
        + _parse_api_key_list(get_env("GOOGLE_AI_API_KEYS"))
        + _parse_api_key_list(get_env("GOOGLE_AI_API_KEY"))
    ):
        if chunk not in seen:
            seen.add(chunk)
            out.append(chunk)
    return out


def get_google_generative_language_api_key() -> Optional[str]:
    """First Generative Language API key (backward compatible)."""
    keys = get_google_generative_language_api_keys()
    return keys[0] if keys else None


def resolve_workflow_llm_provider_and_model(lm_model: str) -> Tuple[str, str]:
    """``(provider, model)`` for :meth:`workflows.adapters.llm.LLMAdapter.call_llm`."""
    if get_workflow_llm_backend() == "google":
        return "gemini", get_google_ai_studio_model()
    return "lm_studio", lm_model


# Common configuration constants
POSTS_DIRECTORY = "datasets/news_cleaned"
METRICS_DIR = REPO_ROOT / "metrics"

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
