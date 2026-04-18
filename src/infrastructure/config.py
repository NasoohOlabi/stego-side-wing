"""Configuration management."""

import os
import re
from pathlib import Path
from typing import Literal

import dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = REPO_ROOT / ".env"

# Load .env file once at module level.
dotenv.load_dotenv(dotenv_path=ENV_FILE_PATH if ENV_FILE_PATH.exists() else None)
_dotenv_values_cache: dict[str, str | None] | None = None


def _load_dotenv_values() -> dict[str, str | None]:
    """Load and cache .env key-values without printing missing-key warnings."""
    global _dotenv_values_cache
    if _dotenv_values_cache is None:
        _dotenv_values_cache = (
            dotenv.dotenv_values(str(ENV_FILE_PATH)) if ENV_FILE_PATH.exists() else {}
        )
    return _dotenv_values_cache


def get_env(key: str, default: str | None = None) -> str | None:
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


def get_lm_studio_url(default: str | None = None) -> str:
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
WorkflowCapacityProfile = Literal["low", "mid", "high"]


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


def get_workflow_capacity_profile() -> WorkflowCapacityProfile:
    """Global capacity preset for workflow fan-out and angle input sizing."""
    raw = (get_env("WORKFLOW_CAPACITY_PROFILE") or "mid").strip().lower()
    if raw in ("low", "mid", "high"):
        return raw
    return "mid"


def _env_non_negative_int(key: str) -> int | None:
    raw = get_env(key)
    if raw is None or not raw.strip():
        return None
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return None


def _capacity_value(key: str, *, low: int, mid: int, high: int) -> int:
    override = _env_non_negative_int(key)
    if override is not None:
        return override
    profile = get_workflow_capacity_profile()
    if profile == "low":
        return low
    if profile == "high":
        return high
    return mid


def get_workflow_research_max_terms() -> int:
    """Maximum normalized search terms kept from term generation."""
    return _capacity_value("WORKFLOW_RESEARCH_MAX_TERMS", low=4, mid=8, high=12)


def get_workflow_research_max_selected_urls() -> int:
    """Maximum unique research URLs selected and fetched per post."""
    return _capacity_value("WORKFLOW_RESEARCH_MAX_SELECTED_URLS", low=12, mid=24, high=48)


def get_workflow_research_fetch_timeout_sec(default: float = 180.0) -> float:
    """Per-attempt timeout for each research URL fetch."""
    raw = get_env("WORKFLOW_RESEARCH_FETCH_TIMEOUT_SEC")
    if not raw:
        return default
    try:
        return max(5.0, min(float(raw.strip()), 3600.0))
    except ValueError:
        return default


def get_workflow_research_fetch_retries(default: int = 1) -> int:
    """Retries after a timed-out fetch attempt."""
    raw = get_env("WORKFLOW_RESEARCH_FETCH_RETRIES")
    if not raw:
        return default
    try:
        return max(0, min(int(raw.strip()), 10))
    except ValueError:
        return default


def get_workflow_research_fetch_concurrency(default: int = 3) -> int:
    """Concurrent URL fetch workers per research post."""
    raw = get_env("WORKFLOW_RESEARCH_FETCH_CONCURRENCY")
    if not raw:
        return default
    try:
        return max(1, min(int(raw.strip()), 16))
    except ValueError:
        return default


def get_workflow_dictionary_max_search_results() -> int:
    """Maximum research text blocks that may enter the shared dictionary."""
    return _capacity_value("WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS", low=12, mid=24, high=48)


def get_workflow_dictionary_max_comments() -> int:
    """Maximum flattened comment bodies that may enter the shared dictionary."""
    return _capacity_value("WORKFLOW_DICTIONARY_MAX_COMMENTS", low=16, mid=32, high=96)


def get_workflow_angles_max_input_blocks() -> int:
    """Maximum total text blocks passed into angle generation / shared dictionary users."""
    return _capacity_value("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", low=24, mid=48, high=128)


def get_workflow_angles_max_output() -> int:
    """Maximum generated angles retained per post."""
    return _capacity_value("WORKFLOW_ANGLES_MAX_OUTPUT", low=8, mid=16, high=32)


def get_workflow_capacity_settings() -> dict[str, str | int]:
    """Structured capacity settings for reports and logs."""
    return {
        "profile": get_workflow_capacity_profile(),
        "research_max_terms": get_workflow_research_max_terms(),
        "research_max_selected_urls": get_workflow_research_max_selected_urls(),
        "dictionary_max_search_results": get_workflow_dictionary_max_search_results(),
        "dictionary_max_comments": get_workflow_dictionary_max_comments(),
        "angles_max_input_blocks": get_workflow_angles_max_input_blocks(),
        "angles_max_output": get_workflow_angles_max_output(),
    }


def _parse_api_key_list(raw: str | None) -> list[str]:
    """Split comma- or whitespace-separated API key tokens."""
    if not raw:
        return []
    return [p for p in (s.strip() for s in re.split(r"[\s,]+", raw.strip())) if p]


def get_google_generative_language_api_keys() -> list[str]:
    """
    API keys for ``generativelanguage.googleapis.com`` (AI Studio / Gemini), in try order.

    Order: ``GOOGLE_PALM_API_KEY`` (if set), then ``GOOGLE_AI_API_KEYS``, then
    ``GOOGLE_AI_API_KEY`` (each of the latter may be comma-separated). Duplicates removed.
    """
    seen: set[str] = set()
    out: list[str] = []
    for chunk in (
        _parse_api_key_list(get_env("GOOGLE_PALM_API_KEY"))
        + _parse_api_key_list(get_env("GOOGLE_AI_API_KEYS"))
        + _parse_api_key_list(get_env("GOOGLE_AI_API_KEY"))
    ):
        if chunk not in seen:
            seen.add(chunk)
            out.append(chunk)
    return out


def get_google_generative_language_api_key() -> str | None:
    """First Generative Language API key (backward compatible)."""
    keys = get_google_generative_language_api_keys()
    return keys[0] if keys else None


def resolve_workflow_llm_provider_and_model(lm_model: str) -> tuple[str, str]:
    """``(provider, model)`` for :meth:`workflows.adapters.llm.LLMAdapter.call_llm`."""
    if get_workflow_llm_backend() == "google":
        return "gemini", get_google_ai_studio_model()
    return "lm_studio", lm_model


def get_workflow_url_fetch_http_first() -> bool:
    """Try plain HTTP + HTML text extraction before Crawl4AI (default: on)."""
    raw = (get_env("WORKFLOW_URL_FETCH_HTTP_FIRST") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_workflow_url_fetch_http_timeout_sec() -> float:
    """Timeout for HTTP-first GET (seconds)."""
    raw = get_env("WORKFLOW_URL_FETCH_HTTP_TIMEOUT_SEC")
    if raw:
        try:
            return max(5.0, min(float(raw.strip()), 120.0))
        except ValueError:
            pass
    return 25.0


def get_workflow_url_fetch_http_min_chars() -> int:
    """Minimum extracted characters to accept HTTP-first text (skip browser crawl)."""
    raw = get_env("WORKFLOW_URL_FETCH_HTTP_MIN_CHARS")
    if raw:
        try:
            return max(80, min(int(raw.strip()), 500_000))
        except ValueError:
            pass
    return 400


def get_crawl4ai_page_timeout_ms() -> int:
    """Browser page timeout for Crawl4AI navigation (milliseconds)."""
    raw = get_env("WORKFLOW_CRAWL4AI_PAGE_TIMEOUT_MS")
    if raw:
        try:
            return max(5000, min(int(raw.strip()), 120_000))
        except ValueError:
            pass
    return 45_000


def get_crawl4ai_magic_enabled() -> bool:
    """Crawl4AI magic mode (overlays, automation). Default on; disable to reduce work."""
    raw = (get_env("WORKFLOW_CRAWL4AI_MAGIC") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


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
