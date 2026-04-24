"""LLM adapter for multiple providers."""

import json
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import openai
import requests
from google import genai
from google.genai import types as genai_types
from google.genai.errors import APIError as GoogleGenaiAPIError
from loguru import logger

from infrastructure.config import (
    REPO_ROOT,
    get_env,
    get_google_generative_language_api_key,
    get_google_generative_language_api_keys,
    get_lm_studio_request_timeout_seconds,
    get_lm_studio_url,
    get_workflow_llm_backend,
)
from infrastructure.json_logging import get_trace_id
from services.workflow_run_tracker import get_run_id
from workflows.utils.debug_probe import write_debug_probe
from workflows.utils.protocol_utils import stable_hash

PROMPTS_LOG_TIMESTAMP = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
PROMPTS_LOG_PATH = REPO_ROOT / "logs" / f"stego_prompts_{PROMPTS_LOG_TIMESTAMP}.log"

_LLM_ADAPTER_LOG = logger.bind(component="LLMAdapter")

_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_RETRYABLE_TRANSPORT_NAME_TOKENS = frozenset(
    {
        "timeout",
        "connection",
        "connect",
        "chunked",
        "remoteprotocol",
        "protocolerror",
        "readerror",
        "writeerror",
        "disconnect",
    }
)
_RETRYABLE_TRANSPORT_MESSAGE_TOKENS = frozenset(
    {
        "server disconnected without sending a response",
        "remote end closed connection without response",
        "connection reset by peer",
        "connection aborted",
        "broken pipe",
    }
)


def _llm_max_attempts() -> int:
    raw = (get_env("LLM_MAX_ATTEMPTS") or "").strip()
    return max(1, int(raw or "3"))


def _llm_retry_backoff_sec(attempt_index: int) -> float:
    base = float((get_env("LLM_RETRY_BACKOFF_BASE_SEC") or "").strip() or "1.0")
    cap = float((get_env("LLM_RETRY_BACKOFF_CAP_SEC") or "").strip() or "30.0")
    return min(cap, base * (2**attempt_index))


def _llm_retry_jitter_sec(wait_sec: float) -> float:
    if wait_sec <= 0:
        return 0.0
    jitter_cap = min(1.0, wait_sec * 0.2)
    return random.uniform(0.0, jitter_cap)


def _exception_status_code(exc: BaseException) -> int | None:
    if isinstance(exc, GoogleGenaiAPIError):
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            return code
    response = getattr(exc, "response", None)
    if response is not None and hasattr(response, "status_code"):
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def _exception_snippet(exc: BaseException, limit: int = 400) -> str:
    if isinstance(exc, GoogleGenaiAPIError):
        msg = getattr(exc, "message", None) or str(exc)
        return msg[:limit] + ("..." if len(msg) > limit else "")
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("text", "content"):
            value = getattr(response, attr, None)
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            if isinstance(value, str) and value:
                return value[:limit] + ("..." if len(value) > limit else "")
    text = str(exc)
    return text[:limit] + ("..." if len(text) > limit else "")


def _gemini_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError("No candidates in Gemini response")
    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        raise RuntimeError("No candidates in Gemini response")
    part0 = parts[0]
    raw = getattr(part0, "text", None) if part0 is not None else None
    if isinstance(raw, str) and raw:
        return raw
    raise RuntimeError("No candidates in Gemini response")


def _gemini_rest_response_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("No candidates in Gemini response")
    first = candidates[0]
    if not isinstance(first, dict):
        raise RuntimeError("No candidates in Gemini response")
    content = first.get("content")
    if not isinstance(content, dict):
        raise RuntimeError("No candidates in Gemini response")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise RuntimeError("No candidates in Gemini response")

    non_thinking: list[str] = []
    fallback: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        fallback.append(text)
        if not bool(part.get("thought")):
            non_thinking.append(text)

    selected = non_thinking or fallback
    if not selected:
        raise RuntimeError("No candidates in Gemini response")
    return "\n".join(selected)


def _genai_generate_text_via_rest(
    *,
    api_key: str,
    model_name: str,
    user_text: str,
    system_message: str | None,
    temperature: float,
    max_tokens: int | None,
    timeout_sec: int,
) -> str:
    endpoint = _provider_endpoint("gemini", model=model_name)
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": temperature,
        },
    }
    if max_tokens is not None:
        payload["generationConfig"]["maxOutputTokens"] = max_tokens
    if system_message:
        payload["system_instruction"] = {"parts": [{"text": system_message}]}

    response = requests.post(
        endpoint,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout_sec,
    )
    response.raise_for_status()
    return _gemini_rest_response_text(response.json())


def _genai_generate_text(
    *,
    api_key: str,
    model_name: str,
    user_text: str,
    system_message: str | None,
    temperature: float,
    max_tokens: int | None,
) -> str:
    client = genai.Client(api_key=api_key)
    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "system_instruction": system_message or None,
    }
    if max_tokens is not None:
        config_kwargs["max_output_tokens"] = max_tokens
    config = genai_types.GenerateContentConfig(**config_kwargs)
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=user_text,
            config=config,
        )
        return _gemini_response_text(resp)
    except Exception as exc:
        if not _is_retryable_transport_error(exc):
            raise
        return _genai_generate_text_via_rest(
            api_key=api_key,
            model_name=model_name,
            user_text=user_text,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=30,
        )


def _fold_system_message_into_prompt(prompt: str, system_message: str) -> str:
    """Preserve system instructions when a provider fallback cannot send them separately."""
    return (
        "System instructions for this request:\n"
        f"{system_message.strip()}\n\n"
        "---\n\n"
        "User request:\n"
        f"{prompt}"
    )


def _is_retryable_llm_error(exc: BaseException) -> bool:
    status = _exception_status_code(exc)
    if status is not None:
        return status in _RETRYABLE_HTTP_STATUSES
    return _is_retryable_transport_error(exc)


def _is_retryable_transport_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    if any(token in name for token in _RETRYABLE_TRANSPORT_NAME_TOKENS):
        return True
    snippet = _exception_snippet(exc).lower()
    return any(token in snippet for token in _RETRYABLE_TRANSPORT_MESSAGE_TOKENS)


def _should_try_next_gemini_api_key(exc: BaseException) -> bool:
    """After retries, rotate to another API key for auth/quota-style failures."""
    status = _exception_status_code(exc)
    if status in (401, 403, 429):
        return True
    if status is None and _is_retryable_transport_error(exc):
        return True
    low = _exception_snippet(exc).lower()
    return any(
        token in low
        for token in (
            "api_key_service_blocked",
            "permission_denied",
            "invalid api key",
            "invalid_api_key",
        )
    )


def _provider_endpoint(
    provider: str, *, lm_studio_url: str | None = None, model: str | None = None
) -> str:
    if provider == "openai":
        return "https://api.openai.com/v1/chat/completions"
    if provider == "gemini":
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model or 'gemini-pro'}:generateContent"
    if provider == "groq":
        return "https://api.groq.com/openai/v1/chat/completions"
    if provider == "lm_studio" and lm_studio_url:
        return f"{lm_studio_url.rstrip('/')}/chat/completions"
    return provider


def _llm_attempt_log_fields(
    *,
    provider: str,
    model: str,
    endpoint: str,
    prompt: str,
    system_message: str | None,
    temperature: float,
    max_tokens: int | None,
    attempt: int,
    attempts_max: int,
) -> dict[str, Any]:
    return {
        "event": "workflow_llm_request",
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "attempt": attempt,
        "attempts_max": attempts_max,
        "retry_count": attempt - 1,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "trace_id": get_trace_id(),
        "run_id": get_run_id(),
        "prompt_hash": stable_hash(prompt),
        "system_prompt_hash": stable_hash(system_message or ""),
    }


def _openai_compatible_meta(
    data: dict[str, Any],
) -> tuple[str | None, int | None, int | None]:
    """finish_reason and token usage from OpenAI-compatible JSON bodies (LM Studio, Groq)."""
    finish_reason: str | None = None
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            fr = ch0.get("finish_reason")
            if fr is not None:
                finish_reason = str(fr)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    usage = data.get("usage")
    if isinstance(usage, dict):
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        if isinstance(pt, int):
            prompt_tokens = pt
        if isinstance(ct, int):
            completion_tokens = ct
    return finish_reason, prompt_tokens, completion_tokens


def _emit_llm_suspicion_logs(
    *,
    provider: str,
    model: str,
    max_tokens: int | None,
    finish_reason: str | None,
    completion_tokens: int | None,
    raw: str,
    thinking: str,
    response: str,
    truncation_suspected: bool,
) -> None:
    """JSONL already holds fields; this surfaces truncation / empty-strip suspicions in app JSONL."""
    tid = str(uuid4())
    if truncation_suspected:
        _LLM_ADAPTER_LOG.bind(
            trace_id=tid,
            log_domain="workflow_llm",
            provider=provider,
            model=model,
            max_tokens=max_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason or "",
        ).warning("llm_finish_reason_length_suspected_truncation")
    if raw.strip() and not response.strip():
        _LLM_ADAPTER_LOG.bind(
            trace_id=str(uuid4()),
            log_domain="workflow_llm",
            provider=provider,
            model=model,
            raw_chars=len(raw),
            thinking_chars=len(thinking),
        ).warning("llm_strip_removed_all_parseable_response")


def _think_pair_patterns() -> tuple[re.Pattern[str], ...]:
    """Long `redacted_thinking` vs short `think`, including mixed open/close."""
    rname, tname = "redacted_thinking", "think"
    lo, lc = f"<{rname}>", f"</{rname}>"
    so, sc = f"<{tname}>", f"</{tname}>"
    pairs = ((lo, lc), (so, sc), (lo, sc), (so, lc))
    return tuple(
        re.compile(
            f"{re.escape(o)}.*?{re.escape(c)}",
            re.DOTALL | re.IGNORECASE,
        )
        for o, c in pairs
    )


_THINK_PAIR_RES = _think_pair_patterns()
_ORPHAN_THINK_CLOSE_RE = re.compile(
    r"</(?:redacted_thinking|think)\s*>",
    re.IGNORECASE,
)

_THINKING_HEADER_PREFIX_RE = re.compile(
    r"^\s*(?:\*\*)?(?:thinking\s+process|chain[-\s]of[-\s]thought)(?:\*\*)?\s*:?",
    re.IGNORECASE,
)
# JSON / decode one-liner / fenced payload — start of model "answer" after prose thinking.
_PAYLOAD_START_LINE_RE = re.compile(
    r"^\s*(?:[\[{]|idx\s*:)",
    re.IGNORECASE,
)


def _first_non_empty_line_index(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.strip():
            return i
    return -1


def _normalize_payload_probe_line(line: str) -> str:
    """Strip leading BOM so payload-start detection sees ``[`` / ``{`` / ``idx:``."""
    return line.lstrip("\ufeff")


def _strip_plain_thinking_prefix(text: str) -> str:
    """Remove leading plain-text 'Thinking Process:' blocks (no XML tags)."""
    plain, rest = _split_plain_thinking_prefix(text)
    return rest if plain else text


def _split_plain_thinking_prefix(text: str) -> tuple[str, str]:
    """Split leading 'Thinking Process:' block from the rest; (\"\", text) if none."""
    lines = text.splitlines()
    i0 = _first_non_empty_line_index(lines)
    if i0 < 0 or not _THINKING_HEADER_PREFIX_RE.match(lines[i0]):
        return "", text
    for j in range(i0 + 1, len(lines)):
        line = lines[j]
        probe = _normalize_payload_probe_line(line)
        if _PAYLOAD_START_LINE_RE.match(probe) or probe.lstrip().startswith("```"):
            prefix = "\n".join(lines[i0:j])
            rest = "\n".join(lines[j:]).strip()
            return prefix, rest
    return "\n".join(lines[i0:]), ""


def _strip_redacted_thinking(text: str) -> str:
    """Remove model chain-of-thought wrappers from assistant text for logs and parsing."""
    s = text.replace("\ufeff", "")
    for _ in range(64):
        prev = s
        for pat in _THINK_PAIR_RES:
            s = pat.sub("", s)
        if s == prev:
            break
    s = _ORPHAN_THINK_CLOSE_RE.sub("", s)
    s = _strip_plain_thinking_prefix(s)
    return s.strip()


strip_redacted_thinking = _strip_redacted_thinking


def _strip_lm_studio_control_envelope(text: str) -> str:
    """Unwrap LM Studio control-token envelopes while leaving other providers untouched."""
    s = text.strip()
    prefix = "<|channel|>final"
    if not s.startswith(prefix):
        return s

    marker = "<|message|>"
    marker_index = s.find(marker)
    if marker_index < 0:
        return s

    payload = s[marker_index + len(marker) :].strip()
    return payload or s


def _split_thinking_and_answer(raw: str) -> tuple[str, str]:
    """Split chain-of-thought from the parseable answer for prompt logs."""
    thinking_parts: list[str] = []
    s = raw
    for _ in range(64):
        prev = s
        for pat in _THINK_PAIR_RES:
            s = pat.sub(
                lambda m, tp=thinking_parts: tp.append(m.group(0)) or "",
                s,
            )
        if s == prev:
            break
    s = _ORPHAN_THINK_CLOSE_RE.sub("", s)
    plain_pre, _ = _split_plain_thinking_prefix(s)
    if plain_pre:
        thinking_parts.append(plain_pre)
    answer = _strip_redacted_thinking(raw)
    thinking = "\n\n".join(thinking_parts).strip()
    return thinking, answer


class LLMAdapter:
    """Adapter for LLM providers (OpenAI, Gemini, Groq, LM Studio)."""

    def __init__(self):
        self.openai_api_key = get_env("OPENAI_API_KEY")
        self.google_generative_language_api_keys: list[str] = (
            get_google_generative_language_api_keys()
        )
        self.google_palm_api_key = get_google_generative_language_api_key()
        self.groq_api_key = get_env("GROQ_API_KEY")
        self.lm_studio_url = get_lm_studio_url()
        self.lm_studio_api_token = get_env("LM_STUDIO_API_TOKEN", "lm-studio")
        self.lm_studio_timeout_sec = get_lm_studio_request_timeout_seconds()
        self.last_call_metadata: dict[str, Any] = {}

    def _log_workflow_llm_turn(
        self,
        provider: str,
        model: str,
        prompt: str,
        system_message: str | None,
        temperature: float,
        max_tokens: int | None,
        assistant_response_raw: str,
        *,
        finish_reason: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        """Append prompt + assistant text for one workflow LLM call to a timestamped log."""
        thinking, response = _split_thinking_and_answer(assistant_response_raw)
        truncation_suspected = finish_reason == "length"
        call_meta = dict(getattr(self, "last_call_metadata", {}))
        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "scope": "workflows",
            "component": "LLMAdapter",
            "trace_id": get_trace_id(),
            "run_id": get_run_id(),
            "provider": provider,
            "model": model,
            "endpoint": call_meta.get("endpoint"),
            "attempt": call_meta.get("attempt"),
            "attempts_max": call_meta.get("attempts_max"),
            "retry_count": call_meta.get("retry_count"),
            "elapsed_ms": call_meta.get("elapsed_ms"),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "finish_reason": finish_reason,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "truncation_suspected": truncation_suspected,
            "raw_chars": len(assistant_response_raw),
            "thinking_chars": len(thinking),
            "response_chars": len(response),
            "system_message": system_message or "",
            "user_prompt": prompt,
            "thinking": thinking,
            "response": response,
            "assistant_response_raw": assistant_response_raw,
            "assistant_response": response,
            "prompt_hash": call_meta.get("prompt_hash"),
            "system_prompt_hash": call_meta.get("system_prompt_hash"),
        }
        try:
            PROMPTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with PROMPTS_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # File append is best-effort; suspicion logs still run below.
            pass
        _emit_llm_suspicion_logs(
            provider=provider,
            model=model,
            max_tokens=max_tokens,
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            raw=assistant_response_raw,
            thinking=thinking,
            response=response,
            truncation_suspected=truncation_suspected,
        )

    def _call_with_retry(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        prompt: str,
        system_message: str | None,
        temperature: float,
        max_tokens: int | None,
        request_fn: Callable[[], str],
    ) -> str:
        attempts = _llm_max_attempts()
        started_at = time.perf_counter()
        last_exc: BaseException | None = None
        for attempt in range(1, attempts + 1):
            self.last_call_metadata = {
                "provider": provider,
                "model": model,
                "endpoint": endpoint,
                "attempt": attempt,
                "attempts_max": attempts,
                "retry_count": attempt - 1,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "trace_id": get_trace_id(),
                "run_id": get_run_id(),
                "prompt_hash": stable_hash(prompt),
                "system_prompt_hash": stable_hash(system_message or ""),
            }
            # region agent log
            write_debug_probe(
                run_id=str(get_run_id() or ""),
                hypothesis_id="H1",
                location="workflows/adapters/llm.py:_call_with_retry:begin",
                message="llm attempt started",
                data={
                    "provider": provider,
                    "model": model,
                    "endpoint": endpoint,
                    "attempt": attempt,
                    "attempts_max": attempts,
                    "retry_count": attempt - 1,
                },
            )
            # endregion
            _LLM_ADAPTER_LOG.info(
                "llm_request_begin",
                extra=_llm_attempt_log_fields(
                    provider=provider,
                    model=model,
                    endpoint=endpoint,
                    prompt=prompt,
                    system_message=system_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    attempt=attempt,
                    attempts_max=attempts,
                ),
            )
            try:
                text = request_fn()
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                self.last_call_metadata.update({"elapsed_ms": elapsed_ms, "success": True})
                # region agent log
                write_debug_probe(
                    run_id=str(get_run_id() or ""),
                    hypothesis_id="H1",
                    location="workflows/adapters/llm.py:_call_with_retry:success",
                    message="llm attempt succeeded",
                    data={
                        "provider": provider,
                        "model": model,
                        "endpoint": endpoint,
                        "attempt": attempt,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                # endregion
                return text
            except Exception as exc:
                last_exc = exc
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                status = _exception_status_code(exc)
                retryable = _is_retryable_llm_error(exc)
                snippet = _exception_snippet(exc)
                self.last_call_metadata.update(
                    {
                        "elapsed_ms": elapsed_ms,
                        "success": False,
                        "error_kind": type(exc).__name__,
                        "http_status": status,
                        "response_snippet": snippet,
                        "retryable": retryable,
                    }
                )
                if retryable and attempt < attempts:
                    wait = _llm_retry_backoff_sec(attempt - 1) + _llm_retry_jitter_sec(
                        _llm_retry_backoff_sec(attempt - 1)
                    )
                    self.last_call_metadata["wait_sec"] = wait
                    # region agent log
                    write_debug_probe(
                        run_id=str(get_run_id() or ""),
                        hypothesis_id="H1",
                        location="workflows/adapters/llm.py:_call_with_retry:retry",
                        message="llm retry scheduled",
                        data={
                            "provider": provider,
                            "model": model,
                            "endpoint": endpoint,
                            "attempt": attempt,
                            "attempts_max": attempts,
                            "http_status": status,
                            "error_kind": type(exc).__name__,
                            "retryable": retryable,
                            "wait_sec": wait,
                        },
                    )
                    # endregion
                    _LLM_ADAPTER_LOG.warning(
                        "llm_request_retry",
                        extra={
                            **self.last_call_metadata,
                            "event": "workflow_llm_retry",
                            "message": "retrying LLM request after transient failure",
                        },
                    )
                    time.sleep(wait)
                    continue
                # region agent log
                write_debug_probe(
                    run_id=str(get_run_id() or ""),
                    hypothesis_id="H1",
                    location="workflows/adapters/llm.py:_call_with_retry:failure",
                    message="llm request failed",
                    data={
                        "provider": provider,
                        "model": model,
                        "endpoint": endpoint,
                        "attempt": attempt,
                        "attempts_max": attempts,
                        "http_status": status,
                        "error_kind": type(exc).__name__,
                        "retryable": retryable,
                        "response_snippet": snippet,
                    },
                )
                # endregion
                _LLM_ADAPTER_LOG.error(
                    "llm_request_failed",
                    extra={
                        **self.last_call_metadata,
                        "event": "workflow_llm_failure",
                        "message": "LLM request failed",
                    },
                )
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM request failed without an exception")

    def call_llm(
        self,
        prompt: str,
        system_message: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """
        Call LLM with prompt.

        Args:
            prompt: User prompt
            system_message: Optional system message
            model: Model name (if None, uses default based on provider)
            provider: 'openai', 'gemini', 'groq', 'lm_studio', or None (auto-select)
            temperature: Temperature setting
            max_tokens: Max tokens to generate

        Returns:
            Generated text
        """
        # Auto-select provider if not specified
        if provider is None:
            provider = self._select_provider()

        if provider == "openai":
            return self._call_openai(prompt, system_message, model, temperature, max_tokens)
        elif provider == "gemini":
            return self._call_gemini(prompt, system_message, model, temperature, max_tokens)
        elif provider == "groq":
            return self._call_groq(prompt, system_message, model, temperature, max_tokens)
        elif provider == "lm_studio":
            return self._call_lm_studio(prompt, system_message, model, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def _select_provider(self) -> str:
        """Select available provider."""
        if get_workflow_llm_backend() == "google":
            if self.google_palm_api_key:
                return "gemini"
            raise RuntimeError(
                "WORKFLOW_LLM_BACKEND selects Google AI Studio but GOOGLE_PALM_API_KEY, "
                "GOOGLE_AI_API_KEYS, and GOOGLE_AI_API_KEY are unset"
            )
        if self.lm_studio_url:
            return "lm_studio"
        if self.openai_api_key:
            return "openai"
        if self.google_palm_api_key:
            return "gemini"
        if self.groq_api_key:
            return "groq"
        raise RuntimeError("No LLM provider configured")

    def _call_openai(
        self,
        prompt: str,
        system_message: str | None,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Call OpenAI API."""
        if not self.openai_api_key:
            raise RuntimeError("OpenAI API key not configured")
        resolved_model = model or "gpt-4"
        endpoint = _provider_endpoint("openai")

        def _request() -> str:
            client = openai.OpenAI(api_key=self.openai_api_key)
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            messages.append({"role": "user", "content": prompt})
            kwargs: dict[str, Any] = {
                "model": resolved_model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            response = client.chat.completions.create(**kwargs)
            choice0 = response.choices[0]
            raw = choice0.message.content or ""
            text = _strip_redacted_thinking(raw)
            fr = getattr(choice0, "finish_reason", None)
            usage = getattr(response, "usage", None)
            pt = getattr(usage, "prompt_tokens", None) if usage else None
            ct = getattr(usage, "completion_tokens", None) if usage else None
            self._log_workflow_llm_turn(
                provider="openai",
                model=resolved_model,
                prompt=prompt,
                system_message=system_message,
                temperature=temperature,
                max_tokens=max_tokens,
                assistant_response_raw=raw,
                finish_reason=str(fr) if fr is not None else None,
                prompt_tokens=int(pt) if isinstance(pt, int) else None,
                completion_tokens=int(ct) if isinstance(ct, int) else None,
            )
            return text

        return self._call_with_retry(
            provider="openai",
            model=resolved_model,
            endpoint=endpoint,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            request_fn=_request,
        )

    def _call_gemini(
        self,
        prompt: str,
        system_message: str | None,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Call Google Gemini API, rotating through configured API keys on auth/quota errors."""
        if not self.google_generative_language_api_keys:
            raise RuntimeError("Google Gemini API key not configured")
        model_name = model or "gemini-pro"
        endpoint = _provider_endpoint("gemini", model=model_name)
        keys = self.google_generative_language_api_keys
        last_exc: BaseException | None = None
        def _make_request(
            resolved_key: str,
            *,
            request_prompt: str,
            request_system_message: str | None,
        ) -> Callable[[], str]:
            def _request() -> str:
                raw = _genai_generate_text(
                    api_key=resolved_key,
                    model_name=model_name,
                    user_text=request_prompt,
                    system_message=request_system_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = _strip_redacted_thinking(raw)
                if not text.strip() and raw.strip():
                    text = raw.strip()
                self._log_workflow_llm_turn(
                    provider="gemini",
                    model=model_name,
                    prompt=request_prompt,
                    system_message=request_system_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    assistant_response_raw=raw,
                )
                return text

            return _request

        def _call_for_key(
            resolved_key: str,
            *,
            request_prompt: str,
            request_system_message: str | None,
        ) -> str:
            return self._call_with_retry(
                provider="gemini",
                model=model_name,
                endpoint=endpoint,
                prompt=request_prompt,
                system_message=request_system_message,
                temperature=temperature,
                max_tokens=max_tokens,
                request_fn=_make_request(
                    resolved_key,
                    request_prompt=request_prompt,
                    request_system_message=request_system_message,
                ),
            )

        for key_index, api_key in enumerate(keys):
            try:
                return _call_for_key(
                    api_key,
                    request_prompt=prompt,
                    request_system_message=system_message,
                )
            except BaseException as exc:
                last_exc = exc
                if system_message and _is_retryable_transport_error(exc):
                    tid = str(uuid4())
                    _LLM_ADAPTER_LOG.bind(
                        trace_id=tid,
                        log_domain="workflow_llm",
                        provider="gemini",
                        key_index=key_index,
                        keys_tried=key_index + 1,
                        keys_total=len(keys),
                        http_status=_exception_status_code(exc),
                    ).info("gemini_retry_with_folded_system_message")
                    try:
                        return _call_for_key(
                            api_key,
                            request_prompt=_fold_system_message_into_prompt(
                                prompt,
                                system_message,
                            ),
                            request_system_message=None,
                        )
                    except BaseException as fallback_exc:
                        last_exc = fallback_exc
                        if key_index + 1 < len(keys) and _should_try_next_gemini_api_key(
                            fallback_exc
                        ):
                            tid = str(uuid4())
                            _LLM_ADAPTER_LOG.bind(
                                trace_id=tid,
                                log_domain="workflow_llm",
                                provider="gemini",
                                key_index=key_index,
                                keys_tried=key_index + 1,
                                keys_total=len(keys),
                                http_status=_exception_status_code(fallback_exc),
                            ).info("gemini_try_next_api_key")
                            continue
                        raise
                if key_index + 1 < len(keys) and _should_try_next_gemini_api_key(exc):
                    tid = str(uuid4())
                    _LLM_ADAPTER_LOG.bind(
                        trace_id=tid,
                        log_domain="workflow_llm",
                        provider="gemini",
                        key_index=key_index,
                        keys_tried=key_index + 1,
                        keys_total=len(keys),
                        http_status=_exception_status_code(exc),
                    ).info("gemini_try_next_api_key")
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Gemini request failed without an exception")

    def _call_groq(
        self,
        prompt: str,
        system_message: str | None,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Call Groq API."""
        if not self.groq_api_key:
            raise RuntimeError("Groq API key not configured")
        url = _provider_endpoint("groq")
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }
        resolved_model = model or "llama3-70b-8192"

        def _request() -> str:
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            messages.append({"role": "user", "content": prompt})
            payload: dict[str, Any] = {
                "model": resolved_model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                payload["max_tokens"] = max_tokens
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("No choices in Groq response")
            raw = choices[0]["message"]["content"] or ""
            text = _strip_redacted_thinking(raw)
            fr, pt, ct = _openai_compatible_meta(data)
            self._log_workflow_llm_turn(
                provider="groq",
                model=resolved_model,
                prompt=prompt,
                system_message=system_message,
                temperature=temperature,
                max_tokens=max_tokens,
                assistant_response_raw=raw,
                finish_reason=fr,
                prompt_tokens=pt,
                completion_tokens=ct,
            )
            return text

        return self._call_with_retry(
            provider="groq",
            model=resolved_model,
            endpoint=url,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            request_fn=_request,
        )

    def _call_lm_studio(
        self,
        prompt: str,
        system_message: str | None,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        """Call LM Studio API."""
        url = _provider_endpoint("lm_studio", lm_studio_url=self.lm_studio_url)
        headers = {
            "Authorization": f"Bearer {self.lm_studio_api_token}",
            "Content-Type": "application/json",
        }
        resolved_model = model or "openai/gpt-oss-20b"

        def _request() -> str:
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            messages.append({"role": "user", "content": prompt})
            payload: dict[str, Any] = {
                "model": resolved_model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens:
                payload["max_tokens"] = max_tokens
            response = requests.post(
                url, headers=headers, json=payload, timeout=self.lm_studio_timeout_sec
            )
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("No choices in LM Studio response")
            raw = choices[0]["message"]["content"] or ""
            text = _strip_lm_studio_control_envelope(_strip_redacted_thinking(raw))
            fr, pt, ct = _openai_compatible_meta(data)
            self._log_workflow_llm_turn(
                provider="lm_studio",
                model=resolved_model,
                prompt=prompt,
                system_message=system_message,
                temperature=temperature,
                max_tokens=max_tokens,
                assistant_response_raw=raw,
                finish_reason=fr,
                prompt_tokens=pt,
                completion_tokens=ct,
            )
            return text

        return self._call_with_retry(
            provider="lm_studio",
            model=resolved_model,
            endpoint=url,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            request_fn=_request,
        )
