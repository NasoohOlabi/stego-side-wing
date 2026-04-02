"""LLM adapter for multiple providers."""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import openai
import requests

from infrastructure.config import (
    REPO_ROOT,
    get_env,
    get_lm_studio_request_timeout_seconds,
    get_lm_studio_url,
)


PROMPTS_LOG_TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
PROMPTS_LOG_PATH = REPO_ROOT / "logs" / f"stego_prompts_{PROMPTS_LOG_TIMESTAMP}.log"

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
    return s.strip()


class LLMAdapter:
    """Adapter for LLM providers (OpenAI, Gemini, Groq, LM Studio)."""

    def __init__(self):
        self.openai_api_key = get_env("OPENAI_API_KEY")
        self.google_palm_api_key = get_env("GOOGLE_PALM_API_KEY")
        self.groq_api_key = get_env("GROQ_API_KEY")
        self.lm_studio_url = get_lm_studio_url()
        self.lm_studio_api_token = get_env("LM_STUDIO_API_TOKEN", "lm-studio")
        self.lm_studio_timeout_sec = get_lm_studio_request_timeout_seconds()

    def _log_workflow_llm_turn(
        self,
        provider: str,
        model: str,
        prompt: str,
        system_message: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
        assistant_response: str,
    ) -> None:
        """Append prompt + assistant text for one workflow LLM call to a timestamped log."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scope": "workflows",
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system_message": system_message or "",
            "user_prompt": prompt,
            "assistant_response": assistant_response,
        }
        try:
            PROMPTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with PROMPTS_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # Logging must be best-effort and never block LLM execution.
            return

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

        client = openai.OpenAI(api_key=self.openai_api_key)

        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        resolved_model = model or "gpt-4"
        kwargs: Dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        response = client.chat.completions.create(**kwargs)
        text = _strip_redacted_thinking(response.choices[0].message.content or "")
        self._log_workflow_llm_turn(
            provider="openai",
            model=resolved_model,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            assistant_response=text,
        )
        return text

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

        # Combine system message and prompt
        full_prompt = prompt
        if system_message:
            full_prompt = f"{system_message}\n\n{prompt}"

        url = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        model_name = model or "gemini-pro"
        url = url.format(model=model_name)

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens

        response = requests.post(
            url,
            params={"key": self.google_palm_api_key},
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()

        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("No candidates in Gemini response")

        text = _strip_redacted_thinking(candidates[0]["content"]["parts"][0]["text"])
        self._log_workflow_llm_turn(
            provider="gemini",
            model=model_name,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            assistant_response=text,
        )
        return text

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

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }

        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        resolved_model = model or "llama3-70b-8192"
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

        text = _strip_redacted_thinking(choices[0]["message"]["content"] or "")
        self._log_workflow_llm_turn(
            provider="groq",
            model=resolved_model,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            assistant_response=text,
        )
        return text

    def _call_lm_studio(
        self,
        prompt: str,
        system_message: Optional[str],
        model: Optional[str],
        temperature: float,
        max_tokens: Optional[int],
    ) -> str:
        """Call LM Studio API."""
        url = f"{self.lm_studio_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.lm_studio_api_token}",
            "Content-Type": "application/json",
        }

        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        # resolved_model = model or "openai/gpt-oss-20b"
        resolved_model = model or "qwen/qwen3.5-9b"
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

        text = _strip_redacted_thinking(choices[0]["message"]["content"] or "")
        self._log_workflow_llm_turn(
            provider="lm_studio",
            model=resolved_model,
            prompt=prompt,
            system_message=system_message,
            temperature=temperature,
            max_tokens=max_tokens,
            assistant_response=text,
        )
        return text
