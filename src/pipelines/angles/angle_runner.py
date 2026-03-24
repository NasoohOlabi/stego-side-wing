from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests

from infrastructure.cache import deterministic_hash_sha256
from infrastructure.config import get_env, get_lm_studio_url
from workflows.utils.text_utils import chunk_text_equal_overlap

ANGLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANGLES_DIR.parent.parent
SYSTEM_PROMPT_PATH = ANGLES_DIR / "systemPrompt.txt"
USER_PROMPT_PATH = ANGLES_DIR / "userPrompt.txt"

ANGLES_CACHE_DIR = REPO_ROOT / "datasets" / "angles_cache"

SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
USER_PROMPT_TEMPLATE = USER_PROMPT_PATH.read_text(encoding="utf-8")


LM_STUDIO_URL = get_lm_studio_url()
LM_STUDIO_API_TOKEN = get_env("LM_STUDIO_API_TOKEN", "lm-studio") or "lm-studio"
CHAT_ENDPOINT = f"{LM_STUDIO_URL.rstrip('/')}/chat/completions"

MODEL_NAME = "openai/gpt-oss-20b"
REQUEST_TIMEOUT = 120
MAX_RESPONSE_TOKENS = 8192
TEMPERATURE = 0

PROMPTS_LOG_PATH = REPO_ROOT / "prompts.log"

# We guard against sending more than this many characters per prompt batch.
MAX_CHARS_PER_PROMPT = 150_000
# We slice long strings into pieces smaller than this to avoid overflowing context.
MAX_CHARS_PER_TEXT = 30_000
SEPARATOR = "\n\n---\n\n"

# On HTTP errors that look like context limits, re-send as N overlapping chunks (no trimming).
CONTEXT_RETRY_NUM_CHUNKS = max(1, int(get_env("ANGLES_CONTEXT_RETRY_CHUNKS", "3") or "3"))
CONTEXT_RETRY_OVERLAP_CHARS = max(0, int(get_env("ANGLES_CONTEXT_RETRY_OVERLAP_CHARS", "5000") or "5000"))


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
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_RESPONSE_TOKENS,
    }

    headers = {
        "Authorization": f"Bearer {LM_STUDIO_API_TOKEN}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        CHAT_ENDPOINT,
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    choices = data.get("choices")
    if not choices:
        raise ValueError("LM Studio returned no choices")

    return choices[0]["message"]["content"]


def _call_llm(prompt_text: str) -> str:
    return _call_llm_with_messages(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ]
    )


def _run_angle_llm_on_batch(
    batch: List[str],
    batch_index: int,
    batch_total: int,
) -> List[Dict[str, str]]:
    user_prompt = _build_user_prompt(batch)
    _log_prompt(user_prompt, batch_index, batch_total, label="angles")
    try:
        answer = _call_llm(user_prompt)
        return _parse_or_repair(answer)
    except requests.HTTPError as e:
        if e.response is None or not _is_context_window_error(e.response):
            raise
        combined = SEPARATOR.join(batch)
        parts = chunk_text_equal_overlap(
            combined,
            CONTEXT_RETRY_NUM_CHUNKS,
            CONTEXT_RETRY_OVERLAP_CHARS,
        )
        merged: List[Dict[str, str]] = []
        for j, part in enumerate(parts, start=1):
            up = _build_user_prompt([part])
            _log_prompt(up, j, len(parts), label=f"angles_ctx_split_{batch_index}")
            answer = _call_llm(up)
            merged.extend(_parse_or_repair(answer))
        return merged


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


def analyze_angles_from_texts(texts: List[str], *, use_cache: bool = True) -> List[Dict[str, str]]:
    all_responses: List[Dict[str, str]] = []

    # Ensure cache directory exists
    ANGLES_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for text in texts:
        if not text:
            continue

        cache_key = deterministic_hash_sha256(text)
        cache_file = ANGLES_CACHE_DIR / f"{cache_key}.json"

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

        batches = _make_batches(segments, MAX_CHARS_PER_PROMPT)
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
