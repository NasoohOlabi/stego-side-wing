"""LLM adapter for multiple providers."""

import json
import re
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

import openai
import requests
from loguru import logger

from infrastructure.config import (
    REPO_ROOT,
    get_env,
    get_lm_studio_request_timeout_seconds,
    get_lm_studio_url,
)
from services.workflow_run_tracker import get_run_id
from workflows.utils.protocol_utils import stable_hash
from infrastructure.json_logging import get_trace_id


PROMPTS_LOG_TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
PROMPTS_LOG_PATH = REPO_ROOT / "logs" / f"stego_prompts_{PROMPTS_LOG_TIMESTAMP}.log"

_LLM_ADAPTER_LOG = logger.bind(component="LLMAdapter")

_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
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


def _exception_status_code(exc: BaseException) -> Optional[int]:
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


def _is_retryable_llm_error(exc: BaseException) -> bool:
    status = _exception_status_code(exc)
    if status is not None:
        return status in _RETRYABLE_HTTP_STATUSES
    name = type(exc).__name__.lower()
    return any(token in name for token in ("timeout", "connection", "connect", "chunked"))


def _provider_endpoint(provider: str, *, lm_studio_url: str | None = None, model: str | None = None) -> str:
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
    system_message: Optional[str],
    temperature: float,
    max_tokens: Optional[int],
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
    data: Dict[str, Any],
) -> tuple[Optional[str], Optional[int], Optional[int]]:
    """finish_reason and token usage from OpenAI-compatible JSON bodies (LM Studio, Groq)."""
    finish_reason: Optional[str] = None
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            fr = ch0.get("finish_reason")
            if fr is not None:
                finish_reason = str(fr)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
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
    max_tokens: Optional[int],
    finish_reason: Optional[str],
    completion_tokens: Optional[int],
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
        if _PAYLOAD_START_LINE_RE.match(line) or line.lstrip().startswith("```"):
            prefix = "\n".join(lines[i0:j])
            rest = "\n".join(lines[j:]).strip()
            return prefix, rest
    return "\n".join(lines[i0:]), ""


def _strip_redacted_thinking(text: str) -> str:
    """Remove model chain-of-thought wrappers from assistant text for logs and parsing."""
    s = text
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
        self.google_palm_api_key = get_env("GOOGLE_PALM_API_KEY")
        self.groq_api_key = get_env("GROQ_API_KEY")
        self.lm_studio_url = get_lm_studio_url()
        self.lm_studio_api_token = get_env("LM_STUDIO_API_TOKEN", "lm-studio")
        self.lm_studio_timeout_sec = get_lm_studio_request_timeout_seconds()
        self.last_call_metadata: Dict[str, Any] = {}

    def _log_workflow_llm_turn(
        self,
        provider: str,
        model: str,
        prompt: str,
        system_message: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        assistant_response_raw: str,
        *,
        finish_reason: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> None:
        """Append prompt + assistant text for one workflow LLM call to a timestamped log."""
        thinking, response = _split_thinking_and_answer(assistant_response_raw)
        truncation_suspected = finish_reason == "length"
        call_meta = dict(getattr(self, "last_call_metadata", {}))
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
        system_message: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
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
            _LLM_ADAPTER_LOG.info("llm_request_begin", extra=_llm_attempt_log_fields(
                provider=provider,
                model=model,
                endpoint=endpoint,
                prompt=prompt,
                system_message=system_message,
                temperature=temperature,
                max_tokens=max_tokens,
                attempt=attempt,
                attempts_max=attempts,
            ))
            try:
                text = request_fn()
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                self.last_call_metadata.update({"elapsed_ms": elapsed_ms, "success": True})
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
        system_message: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
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
            return self._call_openai(
                prompt, system_message, model, temperature, max_tokens
            )
        elif provider == "gemini":
            return self._call_gemini(
                prompt, system_message, model, temperature, max_tokens
            )
        elif provider == "groq":
            return self._call_groq(
                prompt, system_message, model, temperature, max_tokens
            )
        elif provider == "lm_studio":
            return self._call_lm_studio(
                prompt, system_message, model, temperature, max_tokens
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def _select_provider(self) -> str:
        """Select available provider."""
        if self.lm_studio_url:
            return "lm_studio"
        elif self.openai_api_key:
            return "openai"
        elif self.google_palm_api_key:
            return "gemini"
        elif self.groq_api_key:
            return "groq"
        else:
            raise RuntimeError("No LLM provider configured")

    def _call_openai(
        self,
        prompt: str,
        system_message: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
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
            kwargs: Dict[str, Any] = {
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
        system_message: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
    ) -> str:
        """Call Google Gemini API."""
        if not self.google_palm_api_key:
            raise RuntimeError("Google Gemini API key not configured")
        model_name = model or "gemini-pro"
        endpoint = _provider_endpoint("gemini", model=model_name)

        def _request() -> str:
            full_prompt = prompt
            if system_message:
                full_prompt = f"{system_message}\n\n{prompt}"
            payload: Dict[str, Any] = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {"temperature": temperature},
            }
            if max_tokens:
                payload["generationConfig"]["maxOutputTokens"] = max_tokens
            response = requests.post(
                endpoint,
                params={"key": self.google_palm_api_key},
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError("No candidates in Gemini response")
            raw = candidates[0]["content"]["parts"][0]["text"]
            text = _strip_redacted_thinking(raw)
            self._log_workflow_llm_turn(
                provider="gemini",
                model=model_name,
                prompt=prompt,
                system_message=system_message,
                temperature=temperature,
                max_tokens=max_tokens,
                assistant_response_raw=raw,
            )
            return text

        return self._call_with_retry(
            provider="gemini",
            model=model_name,
            endpoint=endpoint,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            request_fn=_request,
        )

    def _call_groq(
        self,
        prompt: str,
        system_message: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
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
            payload: Dict[str, Any] = {
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
        system_message: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
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
            payload: Dict[str, Any] = {
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
            text = _strip_redacted_thinking(raw)
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
