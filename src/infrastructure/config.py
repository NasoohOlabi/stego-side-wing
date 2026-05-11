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


# Workflow LLM (non-sensitive defaults; override WORKFLOW_LLM_BACKEND / GOOGLE_AI_STUDIO_MODEL via env).
DEFAULT_WORKFLOW_LLM_BACKEND = "lm_studio"
DEFAULT_WORKFLOW_LM_STUDIO_MODEL = "qwen/qwen3.5-9b"
DEFAULT_GOOGLE_AI_STUDIO_MODEL = "gemma-4-26b-a4b-it"
DEFAULT_GOOGLE_AI_REQUEST_TIMEOUT_SEC = 180
WorkflowCapacityProfile = Literal["low", "mid", "high"]
WorkflowEncodingProfile = Literal["balanced", "robustness", "capacity", "security"]
WorkflowAnglesGenerationMode = Literal["model", "extractive_zero_kld"]
WorkflowStegoGenerationMode = Literal["model", "extractive_zero_kld", "hybrid_extract"]
WorkflowPayloadTransform = Literal["plain", "hmac_xor_v1", "secure_compact_v2"]
WorkflowStegoPromptStyle = Literal[
    "natural",
    "anchored",
    "guided_natural",
    "natural_then_anchor_retry",
]

# --- Workflow capacity & URL fetch (defaults in code; WORKFLOW_* overrides: process env only) ---
DEFAULT_WORKFLOW_ENCODING_PROFILE: WorkflowEncodingProfile = "balanced"
DEFAULT_WORKFLOW_CAPACITY_PROFILE: WorkflowCapacityProfile = "mid"
DEFAULT_WORKFLOW_ANGLES_GENERATION_MODE: WorkflowAnglesGenerationMode = "model"
DEFAULT_WORKFLOW_STEGO_GENERATION_MODE: WorkflowStegoGenerationMode = "model"
# When WORKFLOW_* on/off env vars are unset, default to this (same semantics as ``=1`` before).
DEFAULT_WORKFLOW_ENV_FLAG_ON = True

# Profile tiers for _capacity_value: (low, mid, high).
WORKFLOW_CAPACITY_TIER_RESEARCH_MAX_TERMS = (4, 8, 12)
WORKFLOW_CAPACITY_TIER_RESEARCH_MAX_SELECTED_URLS = (12, 48, 96)
WORKFLOW_CAPACITY_TIER_DICTIONARY_MAX_SEARCH_RESULTS = (12, 48, 96)
WORKFLOW_CAPACITY_TIER_DICTIONARY_MAX_COMMENTS = (16, 64, 128)
WORKFLOW_CAPACITY_TIER_ANGLES_MAX_INPUT_BLOCKS = (24, 96, 192)
WORKFLOW_CAPACITY_TIER_ANGLES_MAX_OUTPUT = (8, 32, 64)

DEFAULT_WORKFLOW_RESEARCH_FETCH_TIMEOUT_SEC = 180.0
DEFAULT_WORKFLOW_RESEARCH_FETCH_RETRIES = 1
DEFAULT_WORKFLOW_RESEARCH_FETCH_CONCURRENCY = 3

DEFAULT_WORKFLOW_URL_FETCH_HTTP_TIMEOUT_SEC = 25.0
DEFAULT_WORKFLOW_URL_FETCH_HTTP_MIN_CHARS = 400
DEFAULT_WORKFLOW_CRAWL4AI_PAGE_TIMEOUT_MS = 45_000

# When workflow capacity limits are off, _capacity_value returns this for any
# profile-based cap so slices and URL loops effectively never hit the ceiling (> 0 so
# research URL selection never treats the limit as "disabled" / zero-selected).
WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED = 10_000_000
DEFAULT_WORKFLOW_CAPACITY_LIMITS_ENABLED = False

WORKFLOW_ENCODING_PROFILES: dict[WorkflowEncodingProfile, dict[str, object]] = {
    "balanced": {
        "capacity_profile": "mid",
        "capacity_limits_enabled": False,
        "angles_generation_mode": "model",
        "stego_generation_mode": "model",
        "payload_transform": "plain",
        "stego_prompt_style": "natural",
        "stego_sample_angle_count": 1,
        "stego_default_max_retries": 6,
        "decode_semantic_top_n": 20,
        "decode_llm_max_tries": 5,
        "stego_llm_temperature": 0.7,
        "decode_strict_default": False,
    },
    "robustness": {
        "capacity_profile": "mid",
        "capacity_limits_enabled": True,
        "angles_generation_mode": "extractive_zero_kld",
        "stego_generation_mode": "extractive_zero_kld",
        "payload_transform": "plain",
        "stego_prompt_style": "anchored",
        "stego_sample_angle_count": 6,
        "stego_default_max_retries": 8,
        "decode_semantic_top_n": 40,
        "decode_llm_max_tries": 7,
        "stego_llm_temperature": 0.45,
        "decode_strict_default": False,
    },
    "capacity": {
        "capacity_profile": "high",
        "capacity_limits_enabled": True,
        "angles_generation_mode": "extractive_zero_kld",
        "stego_generation_mode": "extractive_zero_kld",
        "payload_transform": "plain",
        "stego_prompt_style": "natural",
        "stego_sample_angle_count": 4,
        "stego_default_max_retries": 4,
        "decode_semantic_top_n": 24,
        "decode_llm_max_tries": 5,
        "stego_llm_temperature": 0.7,
        "decode_strict_default": False,
    },
    "security": {
        "capacity_profile": "high",
        "capacity_limits_enabled": True,
        "angles_generation_mode": "extractive_zero_kld",
        "stego_generation_mode": "extractive_zero_kld",
        "payload_transform": "secure_compact_v2",
        "stego_prompt_style": "anchored",
        "stego_sample_angle_count": 5,
        "stego_default_max_retries": 6,
        "decode_semantic_top_n": 32,
        "decode_llm_max_tries": 7,
        "stego_llm_temperature": 0.55,
        "decode_strict_default": True,
    },
}

WORKFLOW_ENCODING_PROFILE_ALIASES: dict[str, WorkflowEncodingProfile] = {
    "default": "balanced",
    "standard": "balanced",
    "robust": "robustness",
    "reliable": "robustness",
    "high_capacity": "capacity",
    "zero_kld": "capacity",
    "extractive_zero_kld": "capacity",
    "secure": "security",
}


def _workflow_env_raw(key: str) -> str | None:
    """Non-empty ``WORKFLOW_*`` value from the process environment only (not ``.env`` cache)."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def get_workflow_llm_backend() -> Literal["lm_studio", "google"]:
    """Workflow LLM target: Google AI Studio (Generative Language API) or local LM Studio."""
    raw = (_workflow_env_raw("WORKFLOW_LLM_BACKEND") or DEFAULT_WORKFLOW_LLM_BACKEND).lower()
    if raw in ("google", "gemini", "ai_studio"):
        return "google"
    return "lm_studio"


def get_workflow_encoding_profile() -> WorkflowEncodingProfile:
    """Named sender/receiver behavior profile used by workflow stego code."""
    raw = (
        _workflow_env_raw("WORKFLOW_ENCODING_PROFILE") or DEFAULT_WORKFLOW_ENCODING_PROFILE
    ).lower()
    if raw in WORKFLOW_ENCODING_PROFILE_ALIASES:
        return WORKFLOW_ENCODING_PROFILE_ALIASES[raw]
    if raw == "balanced":
        return "balanced"
    if raw == "robustness":
        return "robustness"
    if raw == "capacity":
        return "capacity"
    if raw == "security":
        return "security"
    return DEFAULT_WORKFLOW_ENCODING_PROFILE


def _workflow_encoding_default(key: str) -> object:
    return WORKFLOW_ENCODING_PROFILES[get_workflow_encoding_profile()][key]


def _workflow_encoding_int_default(key: str, fallback: int) -> int:
    value = _workflow_encoding_default(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def _workflow_encoding_float_default(key: str, fallback: float) -> float:
    value = _workflow_encoding_default(key)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def _workflow_env_positive_int(key: str, default: int, *, min_value: int = 1) -> int:
    raw = _workflow_env_raw(key)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(min_value, parsed)


def _workflow_env_float(
    key: str,
    default: float,
    *,
    min_value: float,
    max_value: float,
) -> float:
    raw = _workflow_env_raw(key)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return max(min_value, min(parsed, max_value))


def get_google_ai_studio_model() -> str:
    """Generative Language API model id when workflow LLM backend is AI Studio / Google."""
    return get_env("GOOGLE_AI_STUDIO_MODEL", DEFAULT_GOOGLE_AI_STUDIO_MODEL) or (
        DEFAULT_GOOGLE_AI_STUDIO_MODEL
    )


def get_workflow_lm_studio_model(default: str | None = None) -> str:
    """OpenAI-compatible model id when workflow LLM backend is LM Studio."""
    raw = _workflow_env_raw("WORKFLOW_LM_STUDIO_MODEL")
    if raw:
        return raw
    if default is not None and default.strip():
        return default.strip()
    return DEFAULT_WORKFLOW_LM_STUDIO_MODEL


def get_google_ai_request_timeout_seconds(
    default: int = DEFAULT_GOOGLE_AI_REQUEST_TIMEOUT_SEC,
) -> int:
    """
    HTTP timeout (seconds) for Google AI Studio / Generative Language API requests.

    Override with ``GOOGLE_AI_REQUEST_TIMEOUT_SEC``.
    """
    raw = get_env("GOOGLE_AI_REQUEST_TIMEOUT_SEC")
    if not raw:
        return default
    try:
        n = int(raw.strip())
    except ValueError:
        return default
    return max(10, min(n, 3600))


def get_workflow_capacity_profile() -> WorkflowCapacityProfile:
    """Global capacity preset for workflow fan-out and angle input sizing."""
    default = str(_workflow_encoding_default("capacity_profile"))
    raw = (_workflow_env_raw("WORKFLOW_CAPACITY_PROFILE") or default).lower()
    if raw in ("low", "mid", "high"):
        return raw
    return DEFAULT_WORKFLOW_CAPACITY_PROFILE


def get_workflow_angles_generation_mode() -> WorkflowAnglesGenerationMode:
    """Angle generation strategy for the gen-angles workflow."""
    raw = (
        _workflow_env_raw("WORKFLOW_ANGLES_GENERATION_MODE")
        or str(_workflow_encoding_default("angles_generation_mode"))
    ).lower()
    if raw in ("extractive_zero_kld", "zero_kld", "extractive"):
        return "extractive_zero_kld"
    return "model"


def get_workflow_stego_generation_mode() -> WorkflowStegoGenerationMode:
    """Stego text generation strategy for the final sender output."""
    raw = (
        _workflow_env_raw("WORKFLOW_STEGO_GENERATION_MODE")
        or str(_workflow_encoding_default("stego_generation_mode"))
    ).lower()
    if raw in ("hybrid_extract", "hybrid", "hybrid_extractive"):
        return "hybrid_extract"
    if raw in ("extractive_zero_kld", "zero_kld", "extractive"):
        return "extractive_zero_kld"
    return "model"


def get_workflow_payload_transform() -> WorkflowPayloadTransform:
    """Payload transform applied before codec compression or invisible carrier embedding."""
    raw = (
        _workflow_env_raw("WORKFLOW_PAYLOAD_TRANSFORM")
        or str(_workflow_encoding_default("payload_transform"))
    ).lower()
    if raw in ("secure_compact_v2", "compact_secure_v2", "swsec2", "secure_v2"):
        return "secure_compact_v2"
    if raw in ("hmac_xor_v1", "secure", "xor_hmac"):
        return "hmac_xor_v1"
    return "plain"


def get_workflow_encoding_secret() -> str | None:
    """Optional shared secret for payload transforms that need one."""
    return _workflow_env_raw("WORKFLOW_ENCODING_SECRET") or get_env("WORKFLOW_ENCODING_SECRET")


def get_workflow_stego_prompt_style() -> WorkflowStegoPromptStyle:
    """Prompt contract used by model-based sender generation."""
    raw = (
        _workflow_env_raw("WORKFLOW_STEGO_PROMPT_STYLE")
        or str(_workflow_encoding_default("stego_prompt_style"))
    ).lower()
    if raw in ("guided_natural", "guided", "natural_guided"):
        return "guided_natural"
    if raw in (
        "natural_then_anchor_retry",
        "guided_retry_anchor",
        "natural_retry_anchor",
    ):
        return "natural_then_anchor_retry"
    if raw in ("anchored", "anchor", "recoverable"):
        return "anchored"
    return "natural"


def get_workflow_stego_sample_angle_count() -> int:
    """Number of angle samples sent to source matching / few-shot generation."""
    default = _workflow_encoding_int_default("stego_sample_angle_count", 4)
    return _workflow_env_positive_int("WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT", default)


def get_workflow_stego_default_max_retries() -> int:
    """Default retry budget for sender validation attempts."""
    default = _workflow_encoding_int_default("stego_default_max_retries", 4)
    return _workflow_env_positive_int("WORKFLOW_STEGO_MAX_RETRIES", default, min_value=0)


def get_workflow_decode_semantic_top_n() -> int:
    """Decode semantic shortlist size."""
    default = _workflow_encoding_int_default("decode_semantic_top_n", 20)
    return _workflow_env_positive_int("WORKFLOW_DECODE_SEMANTIC_TOP_N", default)


def get_workflow_decode_llm_max_tries() -> int:
    """Decode LLM retry budget."""
    default = _workflow_encoding_int_default("decode_llm_max_tries", 5)
    return _workflow_env_positive_int("WORKFLOW_DECODE_LLM_MAX_TRIES", default)


def get_workflow_stego_llm_temperature() -> float:
    """Encode/decode LLM sampling temperature for the selected profile."""
    default = _workflow_encoding_float_default("stego_llm_temperature", 0.7)
    return _workflow_env_float(
        "WORKFLOW_STEGO_LLM_TEMPERATURE",
        default,
        min_value=0.0,
        max_value=2.0,
    )


def get_workflow_decode_strict_default() -> bool:
    """Default receiver strict-decode behavior for the selected profile."""
    return _workflow_env_on_off(
        "WORKFLOW_DECODE_STRICT_DEFAULT",
        default=bool(_workflow_encoding_default("decode_strict_default")),
    )


def _workflow_env_on_off(key: str, *, default: bool) -> bool:
    """Unset/empty → default; ``0`` / ``false`` / ``no`` / ``off`` → False."""
    raw = _workflow_env_raw(key)
    if raw is None:
        return default
    return raw.lower() not in ("0", "false", "no", "off")


def get_workflow_capacity_limits_enabled() -> bool:
    """When False, profile presets are ignored; per-key WORKFLOW_* overrides still apply."""
    return _workflow_env_on_off(
        "WORKFLOW_CAPACITY_LIMITS_ENABLED",
        default=bool(_workflow_encoding_default("capacity_limits_enabled")),
    )


def _env_non_negative_int(key: str) -> int | None:
    raw = _workflow_env_raw(key)
    if raw is None:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def _capacity_value(key: str, *, low: int, mid: int, high: int) -> int:
    override = _env_non_negative_int(key)
    if override is not None:
        return override
    if not get_workflow_capacity_limits_enabled():
        return WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    profile = get_workflow_capacity_profile()
    if profile == "low":
        return low
    if profile == "high":
        return high
    return mid


def get_workflow_research_max_terms() -> int:
    """Maximum normalized search terms kept from term generation."""
    low, mid, high = WORKFLOW_CAPACITY_TIER_RESEARCH_MAX_TERMS
    return _capacity_value("WORKFLOW_RESEARCH_MAX_TERMS", low=low, mid=mid, high=high)


def get_workflow_research_max_selected_urls() -> int:
    """Maximum unique research URLs selected and fetched per post."""
    low, mid, high = WORKFLOW_CAPACITY_TIER_RESEARCH_MAX_SELECTED_URLS
    return _capacity_value("WORKFLOW_RESEARCH_MAX_SELECTED_URLS", low=low, mid=mid, high=high)


def get_workflow_research_fetch_timeout_sec(
    default: float = DEFAULT_WORKFLOW_RESEARCH_FETCH_TIMEOUT_SEC,
) -> float:
    """Per-attempt timeout for each research URL fetch."""
    return default


def get_workflow_research_fetch_retries(
    default: int = DEFAULT_WORKFLOW_RESEARCH_FETCH_RETRIES,
) -> int:
    """Retries after a timed-out fetch attempt."""
    return default


def get_workflow_research_fetch_concurrency(
    default: int = DEFAULT_WORKFLOW_RESEARCH_FETCH_CONCURRENCY,
) -> int:
    """Concurrent URL fetch workers per research post."""
    return default


def get_workflow_dictionary_max_search_results() -> int:
    """Maximum research text blocks that may enter the shared dictionary."""
    low, mid, high = WORKFLOW_CAPACITY_TIER_DICTIONARY_MAX_SEARCH_RESULTS
    return _capacity_value("WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS", low=low, mid=mid, high=high)


def get_workflow_dictionary_max_comments() -> int:
    """Maximum flattened comment bodies that may enter the shared dictionary."""
    low, mid, high = WORKFLOW_CAPACITY_TIER_DICTIONARY_MAX_COMMENTS
    return _capacity_value("WORKFLOW_DICTIONARY_MAX_COMMENTS", low=low, mid=mid, high=high)


def get_workflow_angles_max_input_blocks() -> int:
    """Maximum total text blocks passed into angle generation / shared dictionary users."""
    low, mid, high = WORKFLOW_CAPACITY_TIER_ANGLES_MAX_INPUT_BLOCKS
    return _capacity_value("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", low=low, mid=mid, high=high)


def get_workflow_angles_max_output() -> int:
    """Maximum generated angles retained per post."""
    low, mid, high = WORKFLOW_CAPACITY_TIER_ANGLES_MAX_OUTPUT
    return _capacity_value("WORKFLOW_ANGLES_MAX_OUTPUT", low=low, mid=mid, high=high)


def get_workflow_capacity_settings() -> dict[str, str | int | bool]:
    """Structured capacity settings for reports and logs (effective limits after env resolution)."""
    return {
        "limits_enabled": get_workflow_capacity_limits_enabled(),
        "profile": get_workflow_capacity_profile(),
        "research_max_terms": get_workflow_research_max_terms(),
        "research_max_selected_urls": get_workflow_research_max_selected_urls(),
        "dictionary_max_search_results": get_workflow_dictionary_max_search_results(),
        "dictionary_max_comments": get_workflow_dictionary_max_comments(),
        "angles_max_input_blocks": get_workflow_angles_max_input_blocks(),
        "angles_max_output": get_workflow_angles_max_output(),
    }


def get_workflow_encoding_settings() -> dict[str, str | int | float | bool | None]:
    """Structured sender/receiver profile settings for reports and audits."""
    return {
        "encoding_profile": get_workflow_encoding_profile(),
        "capacity_profile": get_workflow_capacity_profile(),
        "capacity_limits_enabled": get_workflow_capacity_limits_enabled(),
        "angles_generation_mode": get_workflow_angles_generation_mode(),
        "stego_generation_mode": get_workflow_stego_generation_mode(),
        "payload_transform": get_workflow_payload_transform(),
        "stego_prompt_style": get_workflow_stego_prompt_style(),
        "stego_sample_angle_count": get_workflow_stego_sample_angle_count(),
        "stego_default_max_retries": get_workflow_stego_default_max_retries(),
        "decode_semantic_top_n": get_workflow_decode_semantic_top_n(),
        "decode_llm_max_tries": get_workflow_decode_llm_max_tries(),
        "stego_llm_temperature": get_workflow_stego_llm_temperature(),
        "decode_strict_default": get_workflow_decode_strict_default(),
        "lm_studio_model": get_workflow_lm_studio_model(),
        "google_ai_studio_model": get_google_ai_studio_model(),
        "has_encoding_secret": bool(get_workflow_encoding_secret()),
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
    return "lm_studio", get_workflow_lm_studio_model(lm_model)


def get_workflow_url_fetch_http_first() -> bool:
    """Try plain HTTP + HTML text extraction before Crawl4AI (default: on)."""
    return DEFAULT_WORKFLOW_ENV_FLAG_ON


def get_workflow_url_fetch_http_timeout_sec() -> float:
    """Timeout for HTTP-first GET (seconds)."""
    return DEFAULT_WORKFLOW_URL_FETCH_HTTP_TIMEOUT_SEC


def get_workflow_url_fetch_http_min_chars() -> int:
    """Minimum extracted characters to accept HTTP-first text (skip browser crawl)."""
    return DEFAULT_WORKFLOW_URL_FETCH_HTTP_MIN_CHARS


def get_crawl4ai_page_timeout_ms() -> int:
    """Browser page timeout for Crawl4AI navigation (milliseconds)."""
    raw = _workflow_env_raw("WORKFLOW_CRAWL4AI_PAGE_TIMEOUT_MS")
    if raw:
        try:
            return max(5000, min(int(raw.strip()), 120_000))
        except ValueError:
            pass
    return DEFAULT_WORKFLOW_CRAWL4AI_PAGE_TIMEOUT_MS


def get_crawl4ai_magic_enabled() -> bool:
    """Crawl4AI magic mode (overlays, automation). Default on; disable to reduce work."""
    return _workflow_env_on_off(
        "WORKFLOW_CRAWL4AI_MAGIC",
        default=DEFAULT_WORKFLOW_ENV_FLAG_ON,
    )


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
