from __future__ import annotations

import datetime
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
from requests.exceptions import (
    ChunkedEncodingError,
    HTTPError,
    ReadTimeout,
    RequestException,
    Timeout,
)
from requests.exceptions import ConnectionError as RequestsConnectionError

from infrastructure.cache import deterministic_hash_sha256
from infrastructure.config import get_env, get_lm_studio_url
from infrastructure.json_logging import TAG_WORKFLOW
from workflows.cache_context import get_angles_cache_dir
from workflows.utils.text_utils import chunk_text_equal_overlap

_LOG = logging.getLogger(__name__)

ANGLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANGLES_DIR.parent.parent
SYSTEM_PROMPT_PATH = ANGLES_DIR / "systemPrompt.txt"
USER_PROMPT_PATH = ANGLES_DIR / "userPrompt.txt"

ANGLES_CACHE_DIR = (
    REPO_ROOT / "datasets" / "angles_cache"
)  # default; live paths use get_angles_cache_dir()

SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
USER_PROMPT_TEMPLATE = USER_PROMPT_PATH.read_text(encoding="utf-8")


LM_STUDIO_URL = get_lm_studio_url()
LM_STUDIO_API_TOKEN = get_env("LM_STUDIO_API_TOKEN", "lm-studio") or "lm-studio"
CHAT_ENDPOINT = f"{LM_STUDIO_URL.rstrip('/')}/chat/completions"


def angles_model_name() -> str:
    """LLM id for angles chat/completions; ANGLES_MODEL overrides MODEL."""
    explicit = (get_env("ANGLES_MODEL") or "").strip()
    if explicit:
        return explicit
    fallback = (get_env("MODEL") or "").strip()
    if fallback:
        return fallback
    # return "openai/gpt-oss-20b"
    return "qwen/qwen3.5-9b"


MODEL_NAME = angles_model_name()
MAX_RESPONSE_TOKENS = 8192
TEMPERATURE = 0

PROMPTS_LOG_PATH = REPO_ROOT / "prompts.log"

# We slice long strings into pieces smaller than this to avoid overflowing context.
MAX_CHARS_PER_TEXT = 30_000
SEPARATOR = "\n\n---\n\n"

# On HTTP errors that look like context limits, re-send as N overlapping chunks (no trimming).
CONTEXT_RETRY_NUM_CHUNKS = max(
    1, int(get_env("ANGLES_CONTEXT_RETRY_CHUNKS", "3") or "3")
)
CONTEXT_RETRY_OVERLAP_CHARS = max(
    0, int(get_env("ANGLES_CONTEXT_RETRY_OVERLAP_CHARS", "5000") or "5000")
)

_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_CONNECTIVITY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ReadTimeout,
    Timeout,
    RequestsConnectionError,
    ChunkedEncodingError,
)


def _env_positive_int(name: str, default: int) -> int:
    raw = (get_env(name) or "").strip()
    if not raw:
        return default
    return max(1, int(raw))


def _env_non_negative_int(name: str, default: int) -> int:
    raw = (get_env(name) or "").strip()
    if not raw:
        return default
    return max(0, int(raw))


def _effective_max_chars_per_prompt() -> int:
    """Batch segments until combined size stays under this (default lowered for flaky tunnels)."""
    raw = (get_env("ANGLES_MAX_CHARS_PER_PROMPT") or "").strip()
    if raw:
        return max(4096, int(raw))
    return 80_000


def _llm_max_attempts() -> int:
    return _env_positive_int("ANGLES_LLM_MAX_ATTEMPTS", 6)


def _llm_retry_backoff_sec(attempt_index: int) -> float:
    """attempt_index 0 = first retry wait."""
    base = float((get_env("ANGLES_LLM_RETRY_BACKOFF_BASE_SEC") or "").strip() or "1.5")
    cap = float((get_env("ANGLES_LLM_RETRY_BACKOFF_CAP_SEC") or "").strip() or "60.0")
    delay = base * (2**attempt_index)
    return min(cap, delay)


def _max_transport_split_depth() -> int:
    return _env_non_negative_int("ANGLES_TRANSPORT_SPLIT_MAX_DEPTH", 24)


def _min_chars_to_split_segment() -> int:
    return max(500, _env_non_negative_int("ANGLES_MIN_SEGMENT_SPLIT_CHARS", 4000))


def _llm_http_timeout() -> float | None:
    """Seconds for requests.post; None = no limit (slow remote LLMs). Optional ANGLES_LLM_REQUEST_TIMEOUT."""
    raw = (get_env("ANGLES_LLM_REQUEST_TIMEOUT") or "").strip()
    if not raw:
        return None
    return float(raw)


def _chat_payload(messages: List[Dict[str, str]]) -> dict[str, Any]:
    return {
        "model": angles_model_name(),
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_RESPONSE_TOKENS,
    }


def _chat_endpoint_for_log() -> str:
    """Host + path only (no secrets); for structured logs."""
    return CHAT_ENDPOINT.split("?", 1)[0]


def _chat_headers() -> dict[str, str]:
    h: dict[str, str] = {
        "Authorization": f"Bearer {LM_STUDIO_API_TOKEN}",
        "Content-Type": "application/json",
    }
    if "ngrok" in CHAT_ENDPOINT.lower():
        h["ngrok-skip-browser-warning"] = "true"
    return h


def _safe_response_body_snippet(response: requests.Response, limit: int = 400) -> str:
    try:
        text = response.text or ""
        return text[:limit] + ("..." if len(text) > limit else "")
    except Exception:
        return ""


def _log_angles_llm_http_error(response: requests.Response) -> None:
    _LOG.error(
        "angles_llm_http_error",
        extra={
            "event": "angles.llm_http_error",
            "tags": [TAG_WORKFLOW],
            "component": "angle_runner",
            "http_status": response.status_code,
            "chat_endpoint": _chat_endpoint_for_log(),
            "model": angles_model_name(),
            "body_snippet": _safe_response_body_snippet(response),
        },
    )


def _assistant_content_from_json(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not choices:
        raise ValueError("LM Studio returned no choices")
    return str(choices[0]["message"]["content"])


def _log_llm_retry(attempt: int, *, reason: str, wait_sec: float) -> None:
    _LOG.info(
        "angles_llm_retry",
        extra={
            "event": "angles.llm_retry",
            "tags": [TAG_WORKFLOW],
            "component": "angle_runner",
            "attempt": attempt,
            "reason": reason,
            "wait_sec": round(wait_sec, 3),
        },
    )


def _retry_after_delay(attempt_idx: int, *, reason: str) -> None:
    wait = _llm_retry_backoff_sec(attempt_idx)
    _log_llm_retry(attempt_idx + 1, reason=reason, wait_sec=wait)
    time.sleep(wait)


def _http_error_should_retry(exc: HTTPError) -> bool:
    if exc.response is None:
        return True
    return exc.response.status_code in _TRANSIENT_HTTP_STATUSES


def _http_retry_reason(exc: HTTPError) -> str:
    if exc.response is None:
        return "http_error_no_response"
    return f"http_{exc.response.status_code}"


def _assistant_from_chat_response(response: requests.Response) -> str | None:
    if response.status_code in _TRANSIENT_HTTP_STATUSES:
        return None
    if response.status_code >= 400:
        _log_angles_llm_http_error(response)
    response.raise_for_status()
    return _assistant_content_from_json(response.json())


def _retry_or_raise_http(attempt: int, exc: HTTPError) -> None:
    if not _http_error_should_retry(exc):
        raise exc
    _retry_after_delay(attempt, reason=_http_retry_reason(exc))


def _log_angles_llm_attempt_start(attempt_one_based: int, attempts_max: int) -> None:
    _LOG.info(
        "angles_llm_request",
        extra={
            "event": "angles.llm_request",
            "tags": [TAG_WORKFLOW],
            "component": "angle_runner",
            "attempt": attempt_one_based,
            "attempts_max": attempts_max,
            "model": angles_model_name(),
            "chat_endpoint": _chat_endpoint_for_log(),
        },
    )


def _run_post_retry_loop(
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float | None,
) -> str:
    attempts = _llm_max_attempts()
    last_err: BaseException | None = None
    for attempt in range(attempts):
        try:
            _log_angles_llm_attempt_start(attempt + 1, attempts)
            response = requests.post(
                CHAT_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            text = _assistant_from_chat_response(response)
            if text is not None:
                return text
            _retry_after_delay(attempt, reason=f"http_{response.status_code}")
        except HTTPError as exc:
            last_err = exc
            _retry_or_raise_http(attempt, exc)
        except _CONNECTIVITY_EXCEPTIONS as exc:
            last_err = exc
            _retry_after_delay(attempt, reason=type(exc).__name__)
    if last_err is not None:
        raise last_err
    raise RuntimeError("angles LLM request failed with no exception recorded")


def _post_chat_response_or_retry(messages: List[Dict[str, str]]) -> str:
    return _run_post_retry_loop(
        _chat_payload(messages),
        _chat_headers(),
        _llm_http_timeout(),
    )


def _chunk_text_at_boundaries(text: str, max_chars: int) -> List[str]:
    """Slice `text` into segments at most `max_chars` without trimming content."""
    if not text:
        return []

    segments: List[str] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(length, start + max_chars)

        if end < length:
            split_at = text.rfind("\n", start, end)
            if split_at <= start:
                split_at = text.rfind(" ", start, end)
            if split_at <= start:
                split_at = end
            end = split_at

        if end <= start:
            end = min(length, start + max_chars)

        segment = text[start:end]
        if segment:
            segments.append(segment)

        if end == start:
            end = start + max_chars
        start = end

    return segments


def _is_context_window_error(response: requests.Response) -> bool:
    if response.status_code == 413:
        return True
    if response.status_code not in (400, 422, 500, 502, 503):
        return False
    blob = (response.text or "").lower()
    try:
        blob += " " + json.dumps(response.json()).lower()
    except Exception:
        pass
    needles = (
        "context",
        "token",
        "length",
        "maximum",
        "too long",
        "exceed",
        "reduce",
        "prompt",
        "context_length",
        "max_tokens",
        "too many",
        "overflow",
        "kv cache",
        "sequence",
    )
    return any(n in blob for n in needles)


def _make_batches(segments: List[str], max_chars: int) -> List[List[str]]:
    batches: List[List[str]] = []
    current_batch: List[str] = []
    current_len = 0

    for segment in segments:
        additional_len = len(segment)
        if current_batch:
            projected_len = current_len + len(SEPARATOR) + additional_len
        else:
            projected_len = current_len + additional_len

        if current_batch and projected_len > max_chars:
            batches.append(current_batch)
            current_batch = []
            current_len = 0
            projected_len = additional_len

        current_batch.append(segment)
        current_len = projected_len

    if current_batch:
        batches.append(current_batch)

    return batches


def _log_prompt(
    prompt_text: str, chunk_index: int, total_chunks: int, label: str = "angles"
) -> None:
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    header = (
        f"[{timestamp}] {label} chunk {chunk_index}/{total_chunks} "
        f"(chars={len(prompt_text)})"
    )
    log_entry = f"{header}\n{prompt_text}\n\n{'-' * 80}\n\n"

    PROMPTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROMPTS_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(log_entry)


def _emit_status(message: str) -> None:
    """Write ASCII-safe status lines without breaking on Windows consoles."""
    try:
        sys.stdout.write(message + "\n")
    except UnicodeEncodeError:
        safe_message = message.encode("ascii", "replace").decode("ascii")
        try:
            sys.stdout.write(safe_message + "\n")
        except Exception:
            return
    except Exception:
        return


def _build_user_prompt(batch: List[str]) -> str:
    combined = SEPARATOR.join(batch).strip()
    if "{{ }}" not in USER_PROMPT_TEMPLATE:
        raise ValueError("user prompt template missing '{{ }}' placeholder")
    return USER_PROMPT_TEMPLATE.replace("{{ }}", combined)


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _schema_errors(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, list):
        return ["Root must be a JSON array."]

    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"Item {idx} must be an object.")
            continue

        for key in ("source_quote", "tangent", "category"):
            if key not in item:
                errors.append(f"Item {idx} missing '{key}'.")
                continue
            if not isinstance(item[key], str):
                errors.append(f"Item {idx} '{key}' must be a string.")

    return errors


def _call_llm_with_messages(messages: List[Dict[str, str]]) -> str:
    return _post_chat_response_or_retry(messages)


def _call_llm(prompt_text: str) -> str:
    return _call_llm_with_messages(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ]
    )


def _transport_sub_batches(batch: List[str]) -> List[List[str]]:
    if len(batch) > 1:
        mid = max(1, len(batch) // 2)
        return [batch[:mid], batch[mid:]]
    text = batch[0]
    min_len = _min_chars_to_split_segment()
    if len(text) <= min_len:
        raise ValueError(
            f"segment too small to split further (len={len(text)} max={min_len})"
        )
    target = max(min_len, (len(text) + 1) // 2)
    parts = _chunk_text_at_boundaries(text, target)
    if len(parts) < 2:
        raise ValueError("chunking produced a single segment")
    return [[p] for p in parts]


def _run_context_window_split(
    batch: List[str], batch_index: int
) -> List[Dict[str, str]]:
    combined = SEPARATOR.join(batch)
    parts = chunk_text_equal_overlap(
        combined,
        CONTEXT_RETRY_NUM_CHUNKS,
        CONTEXT_RETRY_OVERLAP_CHARS,
    )
    merged: List[Dict[str, str]] = []
    for j, part in enumerate(parts, start=1):
        merged.extend(_run_angle_llm_on_batch([part], j, len(parts), _depth=0))
    return merged


def _merge_transport_splits(
    sub_batches: List[List[str]],
    batch_index: int,
    batch_total: int,
    depth: int,
) -> List[Dict[str, str]]:
    _LOG.info(
        "angles_transport_split",
        extra={
            "event": "angles.transport_split",
            "tags": [TAG_WORKFLOW],
            "component": "angle_runner",
            "depth": depth,
            "sub_batches": len(sub_batches),
        },
    )
    merged: List[Dict[str, str]] = []
    for sub in sub_batches:
        merged.extend(
            _run_angle_llm_on_batch(
                sub,
                batch_index,
                batch_total,
                _depth=depth + 1,
            )
        )
    return merged


def _run_angle_llm_on_batch(
    batch: List[str],
    batch_index: int,
    batch_total: int,
    *,
    _depth: int = 0,
) -> List[Dict[str, str]]:
    user_prompt = _build_user_prompt(batch)
    _log_prompt(user_prompt, batch_index, batch_total, label="angles")
    try:
        answer = _call_llm(user_prompt)
        return _parse_or_repair(answer)
    except HTTPError as e:
        if e.response is None or not _is_context_window_error(e.response):
            raise
        return _run_context_window_split(batch, batch_index)
    except RequestException as exc:
        if _depth >= _max_transport_split_depth():
            raise
        try:
            sub_batches = _transport_sub_batches(batch)
        except ValueError:
            raise exc
        return _merge_transport_splits(sub_batches, batch_index, batch_total, _depth)


def _repair_json(raw_text: str, error_message: str, attempt: int) -> str:
    system_prompt = (
        "You are a JSON repair tool. Return ONLY a valid JSON array matching "
        "this schema:\n"
        '{ "type": "array", "items": { "type": "object", "properties": '
        '{ "source_quote": { "type": "string" }, "tangent": { "type": "string" }, '
        '"category": { "type": "string" } }, '
        '"required": ["source_quote", "tangent", "category"] } }\n'
        "No markdown, no commentary, only raw JSON."
    )
    user_prompt = (
        "The previous response failed JSON parsing or schema validation.\n\n"
        f"Error:\n{error_message}\n\n"
        "Original response:\n"
        f"{raw_text}\n\n"
        "Fix the JSON and return ONLY the corrected array."
    )
    _log_prompt(user_prompt, attempt, attempt, label="angles_repair")
    return _call_llm_with_messages(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )


def _parse_or_repair(raw_text: str) -> List[Dict[str, str]]:
    cleaned = _strip_code_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        repaired = _repair_json(cleaned, str(exc), attempt=1)
        cleaned_repair = _strip_code_fences(repaired)
        data = json.loads(cleaned_repair)

    errors = _schema_errors(data)
    if errors:
        repaired = _repair_json(cleaned, "; ".join(errors), attempt=2)
        cleaned_repair = _strip_code_fences(repaired)
        data = json.loads(cleaned_repair)

        errors = _schema_errors(data)
        if errors:
            raise ValueError("Schema validation failed: " + "; ".join(errors))

    return data


def analyze_angles_from_texts(
    texts: List[str], *, use_cache: bool = True
) -> List[Dict[str, str]]:
    all_responses: List[Dict[str, str]] = []

    cache_root = get_angles_cache_dir()
    cache_root.mkdir(parents=True, exist_ok=True)

    for text in texts:
        if not text:
            continue

        cache_key = deterministic_hash_sha256(text)
        cache_file = cache_root / f"{cache_key}.json"

        if use_cache and cache_file.exists():
            _emit_status(f"[angles] cache hit {cache_key[:10]}...")
            try:
                cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
                all_responses.extend(cached_data)
                continue
            except Exception as e:
                _emit_status(f"[angles] cache read error {cache_key[:10]}...: {e}")

        _emit_status(f"[angles] cache miss {cache_key[:10]}...")

        segments = _chunk_text_at_boundaries(text, MAX_CHARS_PER_TEXT)
        if not segments:
            continue

        batches = _make_batches(segments, _effective_max_chars_per_prompt())
        text_responses: List[Dict[str, str]] = []

        for idx, batch in enumerate(batches, start=1):
            validated = _run_angle_llm_on_batch(batch, idx, len(batches))
            text_responses.extend(validated)

        # Cache results for this specific text
        if use_cache:
            try:
                cache_file.write_text(
                    json.dumps(text_responses, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as e:
                _emit_status(f"[angles] cache save error {cache_key[:10]}...: {e}")

        all_responses.extend(text_responses)

    return all_responses


__all__ = ["analyze_angles_from_texts"]
