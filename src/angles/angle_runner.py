from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import requests

ANGLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANGLES_DIR.parent.parent
SYSTEM_PROMPT_PATH = ANGLES_DIR / "systemPrompt.txt"
USER_PROMPT_PATH = ANGLES_DIR / "userPrompt.txt"

SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
USER_PROMPT_TEMPLATE = USER_PROMPT_PATH.read_text(encoding="utf-8")

LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://192.168.100.136:1234/v1")
LM_STUDIO_API_TOKEN = os.environ.get("LM_STUDIO_API_TOKEN", "lm-studio")
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


def _split_long_text(text: str, max_chars: int) -> List[str]:
    trimmed = text.strip()
    if not trimmed:
        return []

    segments: List[str] = []
    start = 0
    length = len(trimmed)

    while start < length:
        end = min(length, start + max_chars)

        if end < length:
            # Try to break on newline or space to keep natural boundaries.
            split_at = trimmed.rfind("\n", start, end)
            if split_at <= start:
                split_at = trimmed.rfind(" ", start, end)
            if split_at <= start:
                split_at = end
            end = split_at

        if end <= start:
            end = min(length, start + max_chars)

        segment = trimmed[start:end].strip()
        if segment:
            segments.append(segment)

        if end == start:
            end = start + max_chars
        start = end

    return segments


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
    timestamp = datetime.datetime.utcnow().isoformat()
    header = (
        f"[{timestamp}] {label} chunk {chunk_index}/{total_chunks} "
        f"(chars={len(prompt_text)})"
    )
    log_entry = f"{header}\n{prompt_text}\n\n{'-' * 80}\n\n"

    PROMPTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PROMPTS_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(log_entry)


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
        CHAT_ENDPOINT, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
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


def analyze_angles_from_texts(texts: List[str]) -> List[Dict[str, str]]:
    segments: List[str] = []
    for text in texts:
        segments.extend(_split_long_text(text, MAX_CHARS_PER_TEXT))

    if not segments:
        raise ValueError("No valid text chunks found for analysis")

    batches = _make_batches(segments, MAX_CHARS_PER_PROMPT)
    responses: List[Dict[str, str]] = []

    for idx, batch in enumerate(batches, start=1):
        user_prompt = _build_user_prompt(batch)
        _log_prompt(user_prompt, idx, len(batches))
        answer = _call_llm(user_prompt)
        validated = _parse_or_repair(answer)
        responses.extend(validated)

    return responses


__all__ = ["analyze_angles_from_texts"]
