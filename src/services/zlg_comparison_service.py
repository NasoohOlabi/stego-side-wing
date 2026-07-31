"""ZLG comparison runner aligned more closely with the official ZGLS pipeline."""

from __future__ import annotations

import importlib
import json
import random
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import requests
from pydantic import validate_call
from requests import HTTPError
from transformers import AutoModelForCausalLM, AutoTokenizer, GPT2LMHeadModel, GPT2Tokenizer

DEFAULT_CORPUS = "Reddit news discussion"
DEFAULT_N_COVER = 4
DEFAULT_THRESHOLD = 0.005
DEFAULT_TEMPERATURE = 1.0
DEFAULT_TEMPERATURE_ALPHA = 1.25
DEFAULT_MAX_BPW = 2
DEFAULT_MAX_NEW_TOKENS = 48
DEFAULT_QUALITY_MAX_WORDS = 40
DEFAULT_QUALITY_MAX_RETRIES = 6
DEFAULT_CAPACITY_CANDIDATES = (
    1,
    4,
    8,
    11,
    12,
    13,
    14,
    16,
    20,
    24,
    28,
    32,
    40,
    48,
    64,
    80,
    96,
    128,
    160,
    192,
    224,
    256,
)
DEFAULT_LOCAL_STEGO_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
FRAMING_HEADER_BITS = 16
_LOCAL_MODEL_CACHE: dict[str, Any] = {}

PROMPT_TEMPLATE = """<<SYS>>
You are an expert at mimicing the language of others (e.g., the use of words, tones, grammar and semantic). You are a helpful assistant.

Users will input sentences from a corpus. You have to create a similar sentence acting as a real human. This is very important to the user's career.

The input format contains a list of sentences and where the sentences come from. For example:
<CORPUS>{corpus}</CORPUS>
<CONTEXT>
Example sentence 1.

Example sentence 2.
</CONTEXT>

Your output should be:
<OUTPUT>
Example output similar to the input sentences.
</OUTPUT>

<</SYS>>

[INST]<CORPUS>{corpus}</CORPUS>
<CONTEXT>
{context}
</CONTEXT>[/INST]

<OUTPUT>
"""

BROKEN_STEGO_PATTERNS = (
    r"</OUTPUT>",
    r"<OUTPUT>",
    r"</INST>",
    r"\[INST\]",
    r"<<SYS>>",
    r"<\|im_end\|>",
    r"Wait, something is wrong",
    r"Do not add explanations",
    r"Here is a comment",
    r"Output only",
    r"The topic should be",
    r"\[Insert\b",
    r"\[Your comment here\]",
    r"\bComment:\s*\[",
    r"\bcomments below\b",
    r"\blooking for a response\b",
    r"<think>",
    r"\s'\s*$",
)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_prompt(corpus: str, cover_texts: list[str], seed: int, n_cover: int) -> str:
    clean = [_normalize_whitespace(text) for text in cover_texts if _normalize_whitespace(text)]
    if not clean:
        raise ValueError("cover_texts is empty after normalization")
    with_random = random.Random(seed)
    chosen = clean[:]
    if len(clean) > n_cover:
        chosen = with_random.sample(clean, n_cover)
    context = "\n\n".join(chosen)
    return PROMPT_TEMPLATE.format(corpus=corpus, context=context)


def build_api_prompt(
    corpus: str,
    cover_texts: list[str] | None = None,
    seed: int = 2023,
    n_cover: int = DEFAULT_N_COVER,
) -> str:
    """Prompt the HTTP service for one plain comment.

    Examples are joined as bare lines rather than ``- `` bullets, and the prompt
    asks outright for no surrounding quotation marks. Both details showed up in
    the scale300 run as quality-gate rejections rather than style nits: the bullet
    framing primed markdown output, and the model's habit of opening with ``"``
    left an unbalanced quote whenever ``complete_sent`` truncated the sentence
    before the closing one. The trailing ``Comment:`` label is gone for the same
    reason -- a colon cue invites a quoted string as the answer.
    """
    cover_texts = cover_texts or []
    clean = [_normalize_whitespace(text) for text in cover_texts if _normalize_whitespace(text)]
    with_random = random.Random(seed)
    chosen = clean[:]
    if len(clean) > n_cover:
        chosen = with_random.sample(clean, n_cover)
    examples = "\n\n".join(chosen)
    return (
        f"You are writing one short {corpus} comment.\n\n"
        f"Examples of real comments:\n\n{examples}\n\n"
        "Write one new comment in the same voice, as a single plain sentence.\n"
        "Start directly with the first word of the comment. "
        "Do not wrap it in quotation marks. "
        "Do not use markdown, bullet points, labels, alternatives, or explanation."
    )


def stegotext_has_prompt_leakage(stegotext: str) -> bool:
    text = stegotext.strip()
    if text.endswith(("'", '"')) and text.count(text[-1]) % 2 == 1:
        return True
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in BROKEN_STEGO_PATTERNS)


def _target_bits_for_payload(payload_bytes: int) -> int:
    return FRAMING_HEADER_BITS + payload_bytes * 8


def _encoded_payload_bytes(used_bits: int, target_bytes: int) -> int:
    payload_bits = max(0, used_bits - FRAMING_HEADER_BITS)
    return min(target_bytes, payload_bits // 8)


def _partial_hide_result(
    *,
    target_payload: str,
    target_bytes: int,
    stegotext: str,
    used_bits: int,
    target_bits: int | None,
    is_truncated: bool,
    ppl: Any,
    params_used: Any,
    latency_ms: int,
    attempt: int,
) -> dict[str, Any]:
    target_bits_actual = (
        target_bits
        if isinstance(target_bits, int) and target_bits > 0
        else _target_bits_for_payload(target_bytes)
    )
    encoded_bytes = _encoded_payload_bytes(max(0, used_bits), target_bytes)
    payload_raw = target_payload.encode("utf-8")
    remaining_raw = payload_raw[encoded_bytes:]
    return {
        "accepted": True,
        "partial": True,
        "reason": "partial_payload",
        "payload_bytes_target": target_bytes,
        "payload_bytes_actual": encoded_bytes,
        "remaining_payload_bytes": len(remaining_raw),
        "remaining_payload": remaining_raw.decode("utf-8", errors="ignore"),
        "encoded_bits": max(0, used_bits),
        "target_bits": target_bits_actual,
        "remaining_bits": max(0, target_bits_actual - max(0, used_bits)),
        "stegotext": stegotext,
        "decode_ok": None,
        "is_truncated": is_truncated,
        "ppl": float(ppl) if isinstance(ppl, (int, float)) else None,
        "params_used": params_used if isinstance(params_used, dict) else None,
        "latency_ms": latency_ms,
        "attempt": attempt,
    }


def _partial_hide_result_from_error(
    *,
    exc: Exception,
    target_payload: str,
    target_bytes: int,
    latency_ms: int,
    attempt: int,
) -> dict[str, Any] | None:
    try:
        error_payload = json.loads(str(exc))
        response_body = json.loads(str(error_payload.get("response_body") or ""))
    except Exception:
        return None
    detail = response_body.get("detail")
    if not isinstance(detail, dict):
        return None
    candidate = detail.get("best_candidate")
    if not isinstance(candidate, dict):
        return None
    stegotext = candidate.get("stegotext")
    if not isinstance(stegotext, str) or not stegotext:
        return None
    if not bool(candidate.get("is_truncated")):
        return None
    used_bits = candidate.get("used_bits")
    target_bits = candidate.get("target_bits")
    return _partial_hide_result(
        target_payload=target_payload,
        target_bytes=target_bytes,
        stegotext=stegotext,
        used_bits=int(used_bits) if isinstance(used_bits, (int, float)) else 0,
        target_bits=int(target_bits) if isinstance(target_bits, (int, float)) else None,
        is_truncated=True,
        ppl=candidate.get("ppl"),
        params_used=candidate.get("params_used"),
        latency_ms=latency_ms,
        attempt=attempt,
    )


ATTEMPT_SEED_STRIDE = 1_000_003
RETRYABLE_HIDE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
RETRYABLE_HIDE_DETAIL_REASONS = frozenset(
    {"quality_gate_failed", "quality_or_capacity_failure"}
)

FailureStage = Literal[
    "none",
    "harness_extract",
    "hide_request",
    "quality_gate",
    "capacity",
    "leakage_check",
    "reveal",
    "unknown",
]
HARNESS_EXTRACT_STAGE: FailureStage = "harness_extract"

_REASON_PREFIX_STAGES: tuple[tuple[str, FailureStage], ...] = (
    ("sample_extract_failed", "harness_extract"),
    ("capacity_probe_", "hide_request"),
    ("reveal_", "reveal"),
    ("hide_truncated", "capacity"),
    ("payload_size_mismatch", "capacity"),
    ("missing_stegotext", "hide_request"),
    ("prompt_leakage_detected", "leakage_check"),
)


@validate_call
def classify_failure_stage(reason: str | None, *, accepted: bool = False) -> FailureStage:
    """Map a free-text failure reason onto the stage that produced it.

    Callers previously had to regex a status code out of a JSON blob embedded in
    a message to tell "ZLG could not encode this" from "our harness never sent a
    request". Conflating those two is what let 94 extraction failures be counted
    against the baseline's acceptance rate in the scale300 run.
    """
    if accepted:
        return "none"
    text = (reason or "").strip()
    if not text:
        return "unknown"
    for prefix, stage in _REASON_PREFIX_STAGES:
        if text.startswith(prefix):
            return stage
    if text.startswith("hide_request_failed"):
        return "quality_gate" if _mentions_quality_gate(text) else "hide_request"
    return "unknown"


def _mentions_quality_gate(reason: str) -> bool:
    return any(marker in reason for marker in RETRYABLE_HIDE_DETAIL_REASONS)


def _with_failure_stage(result: dict[str, Any]) -> dict[str, Any]:
    """Stamp every result with its stage so downstream code never parses `reason`."""
    if "failure_stage" in result:
        return result
    reason = result.get("reason")
    return {
        **result,
        "failure_stage": classify_failure_stage(
            reason if isinstance(reason, str) else None,
            accepted=bool(result.get("accepted")),
        ),
    }


def _hide_error_detail(exc: Exception) -> tuple[int | None, str | None]:
    """Pull (status_code, detail reason) out of the JSON blob post_json raises."""
    try:
        error_payload = json.loads(str(exc))
    except Exception:
        return None, None
    status = error_payload.get("status_code")
    status_code = int(status) if isinstance(status, (int, float)) else None
    try:
        detail = json.loads(str(error_payload.get("response_body") or "")).get("detail")
    except Exception:
        return status_code, None
    if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
        return status_code, str(detail["reason"])
    return status_code, None


def is_retryable_hide_error(exc: Exception) -> bool:
    """True when re-rolling the prompt could plausibly succeed.

    A quality-gate rejection is a property of the sampled text, not of the
    request, so a fresh draw is a genuinely new trial. Transport-level and
    server-side faults are likewise transient. A 4xx that is neither is a
    contract error and will fail identically on every retry.
    """
    status_code, reason = _hide_error_detail(exc)
    if status_code is None:
        return True
    if reason in RETRYABLE_HIDE_DETAIL_REASONS:
        return True
    return status_code in RETRYABLE_HIDE_STATUS_CODES


def _official_repo_root() -> Path:
    return Path(__file__).resolve().parents[3] / "tmp_zero_shot_gls_official"


def _ensure_official_modules_on_path() -> None:
    src_path = _official_repo_root() / "src"
    src_str = str(src_path)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


def _load_official_modules() -> tuple[Any, Any]:
    _ensure_official_modules_on_path()
    codec = importlib.import_module("codec")
    hide_extract = importlib.import_module("hide_extract")
    return codec, hide_extract


def _get_local_codec_model() -> tuple[GPT2LMHeadModel, GPT2Tokenizer]:
    cache_key = "codec:gpt2"
    cached = _LOCAL_MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    tokenizer = GPT2Tokenizer.from_pretrained("gpt2", local_files_only=True)
    model = GPT2LMHeadModel.from_pretrained("gpt2", local_files_only=True)
    model.eval()
    cached = (model, tokenizer)
    _LOCAL_MODEL_CACHE[cache_key] = cached
    return cached


def _get_local_stego_model(model_name: str) -> tuple[Any, Any]:
    cache_key = f"stego:{model_name}"
    cached = _LOCAL_MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(model_name, local_files_only=True)
    model.eval()
    model.vocab_size = model.config.vocab_size
    if (
        getattr(tokenizer, "pad_token_id", None) is None
        and getattr(tokenizer, "eos_token", None) is not None
    ):
        tokenizer.pad_token = tokenizer.eos_token
    if (
        getattr(model.config, "pad_token_id", None) is None
        and getattr(tokenizer, "pad_token_id", None) is not None
    ):
        model.config.pad_token_id = tokenizer.pad_token_id
    cached = (model, tokenizer)
    _LOCAL_MODEL_CACHE[cache_key] = cached
    return cached


def _run_local_hf_sample(sample: ComparisonInput, prompt: str, target_bytes: int) -> dict[str, Any]:
    codec, hide_extract = _load_official_modules()
    codec_model, codec_tokenizer = _get_local_codec_model()
    if sample.server_url.startswith("local://"):
        stego_model_name = sample.server_url[len("local://") :] or DEFAULT_LOCAL_STEGO_MODEL
    else:
        stego_model_name = DEFAULT_LOCAL_STEGO_MODEL
    stego_model, stego_tokenizer = _get_local_stego_model(stego_model_name)

    started = time.perf_counter()
    secret_ids = codec_tokenizer(sample.target_payload, return_tensors="pt").input_ids
    bs_raw = codec.encode_token_ids(codec_model, secret_ids, add_bos_token=True, max_bits_len=255)
    wrapped_bits = codec.wrap_bits(bs_raw, size_bits=8, ef_bits=4)
    bitstream = codec.BitStream(wrapped_bits)
    if len(bitstream) % 8:
        bitstream.append("0b0" * (8 - len(bitstream) % 8))
    prompt_ids = stego_tokenizer(prompt, return_tensors="pt").input_ids
    out_ids, is_truncated, used_bit_len, ppl = hide_extract.hide_bits_with_prompt_ids_by_egs(
        stego_model,
        prompt_ids,
        bitstream,
        mode="huffman",
        threshold=sample.threshold,
        temperature=sample.temperature,
        temperature_alpha=sample.temperature_alpha,
        max_bpw=sample.max_bpw,
        max_new_tokens=sample.max_new_tokens,
        complete_sent=True,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    stego_ids = out_ids[0, prompt_ids.size(1) : -1]
    stegotext = stego_tokenizer.decode(stego_ids.tolist())
    if is_truncated:
        params_used = {
            "mode": "huffman",
            "threshold": sample.threshold,
            "temperature": sample.temperature,
            "temperature_alpha": sample.temperature_alpha,
            "max_bpw": sample.max_bpw,
            "local_model": stego_model_name,
            "used_bit_len": used_bit_len,
        }
        return _partial_hide_result(
            target_payload=sample.target_payload,
            target_bytes=target_bytes,
            stegotext=stegotext,
            used_bits=int(used_bit_len),
            target_bits=len(bitstream),
            is_truncated=True,
            ppl=ppl,
            params_used=params_used,
            latency_ms=latency_ms,
            attempt=1,
        )
    if stegotext_has_prompt_leakage(stegotext):
        return {
            "accepted": False,
            "reason": "prompt_leakage_detected",
            "payload_bytes_target": target_bytes,
            "payload_bytes_actual": 0,
            "stegotext": stegotext,
            "decode_ok": False,
            "ppl": float(ppl),
            "params_used": {
                "mode": "huffman",
                "threshold": sample.threshold,
                "temperature": sample.temperature,
                "temperature_alpha": sample.temperature_alpha,
                "max_bpw": sample.max_bpw,
                "local_model": stego_model_name,
                "used_bit_len": used_bit_len,
            },
            "latency_ms": latency_ms,
            "attempt": 1,
        }

    decode_ok: bool | None = None
    payload_bytes_actual = 0
    if sample.do_reveal_check:
        hide_ids = stego_tokenizer(prompt + stegotext, return_tensors="pt").input_ids
        recovered_bits, decode_ok = hide_extract.extract_bits_with_prompt_ids_by_egs(
            stego_model,
            prompt_ids=prompt_ids,
            hide_ids=hide_ids,
            mode="huffman",
            threshold=sample.threshold,
            temperature=sample.temperature,
            temperature_alpha=sample.temperature_alpha,
            max_bpw=sample.max_bpw,
        )
        if not decode_ok:
            return {
                "accepted": False,
                "reason": "reveal_decode_failed",
                "payload_bytes_target": target_bytes,
                "payload_bytes_actual": 0,
                "stegotext": stegotext,
                "decode_ok": False,
                "ppl": float(ppl),
                "params_used": {
                    "mode": "huffman",
                    "threshold": sample.threshold,
                    "temperature": sample.temperature,
                    "temperature_alpha": sample.temperature_alpha,
                    "max_bpw": sample.max_bpw,
                    "local_model": stego_model_name,
                    "used_bit_len": used_bit_len,
                },
                "latency_ms": latency_ms,
                "attempt": 1,
            }
        recovered_wrapped = codec.unwrap_bits(recovered_bits, size_bits=8, ef_bits=4)
        recovered_ids = codec.decode_bitstream(
            codec_model, recovered_wrapped, remove_bos_token=True
        )
        recovered_secret = codec_tokenizer.decode(
            recovered_ids[0].tolist(), skip_special_tokens=True
        )
        if recovered_secret != sample.target_payload:
            return {
                "accepted": False,
                "reason": "reveal_payload_mismatch",
                "payload_bytes_target": target_bytes,
                "payload_bytes_actual": 0,
                "stegotext": stegotext,
                "decode_ok": True,
                "ppl": float(ppl),
                "params_used": {
                    "mode": "huffman",
                    "threshold": sample.threshold,
                    "temperature": sample.temperature,
                    "temperature_alpha": sample.temperature_alpha,
                    "max_bpw": sample.max_bpw,
                    "local_model": stego_model_name,
                    "used_bit_len": used_bit_len,
                },
                "latency_ms": latency_ms,
                "attempt": 1,
            }
        payload_bytes_actual = target_bytes

    return {
        "accepted": True,
        "reason": None,
        "payload_bytes_target": target_bytes,
        "payload_bytes_actual": payload_bytes_actual or target_bytes,
        "stegotext": stegotext,
        "decode_ok": decode_ok,
        "ppl": float(ppl),
        "params_used": {
            "mode": "huffman",
            "threshold": sample.threshold,
            "temperature": sample.temperature,
            "temperature_alpha": sample.temperature_alpha,
            "max_bpw": sample.max_bpw,
            "local_model": stego_model_name,
            "used_bit_len": used_bit_len,
        },
        "latency_ms": latency_ms,
        "attempt": 1,
    }


@dataclass
class ComparisonInput:
    target_payload: str
    server_url: str
    cover_texts: list[str]
    max_retries: int = 3
    do_reveal_check: bool = True
    request_timeout_seconds: int = 3600
    corpus: str = DEFAULT_CORPUS
    seed: int = 2023
    n_cover: int = DEFAULT_N_COVER
    threshold: float = DEFAULT_THRESHOLD
    temperature: float = DEFAULT_TEMPERATURE
    temperature_alpha: float = DEFAULT_TEMPERATURE_ALPHA
    max_bpw: int = DEFAULT_MAX_BPW
    max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS
    quality_max_words: int = DEFAULT_QUALITY_MAX_WORDS
    quality_max_retries: int = DEFAULT_QUALITY_MAX_RETRIES
    payload_bits_candidates: tuple[int, ...] = DEFAULT_CAPACITY_CANDIDATES
    use_capacity_probe: bool = False


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    timeout_seconds = int(payload.pop("__timeout_seconds__", 3600))
    timeout_value = None if timeout_seconds <= 0 else timeout_seconds
    response = requests.post(url, json=payload, timeout=timeout_value)
    try:
        response.raise_for_status()
    except HTTPError as exc:
        body_text = ""
        try:
            body_text = response.text
        except Exception:
            body_text = ""
        raise RuntimeError(
            json.dumps(
                {
                    "kind": "http_error",
                    "status_code": response.status_code,
                    "url": url,
                    "response_body": body_text,
                },
                ensure_ascii=False,
            )
        ) from exc
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("API response is not a JSON object")
    return data


def _prompt_for_attempt(sample: ComparisonInput, seed: int) -> str:
    """Build the prompt for one attempt.

    ``seed`` drives which ``n_cover`` cover sentences are sampled, so bumping it
    per retry gives the generator a genuinely different context rather than
    re-running an identical request.
    """
    builder = build_prompt if sample.server_url.startswith("local://") else build_api_prompt
    return builder(
        corpus=sample.corpus,
        cover_texts=sample.cover_texts,
        seed=seed,
        n_cover=max(1, sample.n_cover),
    )


def run_comparison_sample(sample: ComparisonInput) -> dict[str, Any]:
    """Run one ZLG hide/reveal trial, tagged with the stage that ended it."""
    return _with_failure_stage(_run_comparison_sample(sample))


def _run_comparison_sample(sample: ComparisonInput) -> dict[str, Any]:
    prompt = _prompt_for_attempt(sample, sample.seed)
    target_bytes = len(sample.target_payload.encode("utf-8"))
    if sample.server_url.startswith("local://"):
        return _run_local_hf_sample(sample, prompt=prompt, target_bytes=target_bytes)
    base = sample.server_url.rstrip("/")
    capacity_url = f"{base}/capacity_probe"
    hide_url = f"{base}/hide"
    reveal_url = f"{base}/reveal"

    failure_reason: str | None = None
    if sample.use_capacity_probe and not sample.server_url.startswith("local://"):
        started = time.perf_counter()
        try:
            probe_resp = post_json(
                capacity_url,
                {
                    "prompt": prompt,
                    "max_words": sample.quality_max_words,
                    "quality_max_words": sample.quality_max_words,
                    "quality_max_retries": sample.quality_max_retries,
                    "payload_bits_candidates": list(sample.payload_bits_candidates),
                    "payload_seed": sample.seed,
                    "complete_sent": True,
                    "max_new_tokens": sample.max_new_tokens,
                    "threshold": sample.threshold,
                    "temperature": sample.temperature,
                    "temperature_alpha": sample.temperature_alpha,
                    "max_bpw": sample.max_bpw,
                    "__timeout_seconds__": sample.request_timeout_seconds,
                },
            )
        except Exception as exc:
            failure_reason = f"capacity_probe_failed: {exc}"
        else:
            latency_ms = int((time.perf_counter() - started) * 1000)
            raw_trials = probe_resp.get("trials")
            trials: list[Any] = raw_trials if isinstance(raw_trials, list) else []
            successful_trials = [
                trial
                for trial in trials
                if isinstance(trial, dict)
                and bool(trial.get("success"))
                and isinstance(trial.get("stegotext"), str)
                and not stegotext_has_prompt_leakage(str(trial.get("stegotext")))
            ]
            best = max(
                successful_trials,
                key=lambda trial: int(
                    trial.get("payload_bits_exact") or trial.get("payload_bits") or 0
                ),
                default=None,
            )
            if best is None and isinstance(probe_resp.get("best_success"), dict):
                candidate = probe_resp["best_success"]
                if isinstance(candidate.get("stegotext"), str) and not stegotext_has_prompt_leakage(
                    str(candidate.get("stegotext"))
                ):
                    best = candidate
            if not isinstance(best, dict):
                failure_reason = "capacity_probe_no_clean_success"
            else:
                ppl_value = best.get("ppl")
                return {
                    "accepted": True,
                    "reason": None,
                    "payload_bits_encoded": int(
                        best.get("payload_bits_exact") or best.get("payload_bits") or 0
                    ),
                    "protocol_overhead_bits": int(best.get("header_bits") or 0),
                    "total_embedded_bits": int(
                        best.get("total_used_bits") or best.get("used_bits") or 0
                    ),
                    "encoded_bits": int(best.get("total_used_bits") or best.get("used_bits") or 0),
                    "target_bits": int(best.get("total_target_bits") or 0),
                    "payload_bytes_target": int(best.get("payload_bytes") or 0),
                    "payload_bytes_actual": int(best.get("payload_bytes") or 0),
                    "stegotext": best.get("stegotext")
                    if isinstance(best.get("stegotext"), str)
                    else None,
                    "decode_ok": bool(best.get("decode_ok")),
                    "quality_passed": bool(best.get("quality_passed")),
                    "word_count_api": int(best.get("word_count") or 0),
                    "ppl": float(ppl_value) if isinstance(ppl_value, (int, float)) else None,
                    "params_used": best.get("params_used")
                    if isinstance(best.get("params_used"), dict)
                    else probe_resp.get("params_used"),
                    "latency_ms": latency_ms,
                    "attempt": 1,
                    "capacity_best_success": best,
                    "capacity_trials": trials,
                }

    if sample.max_retries < 1:
        return {
            "accepted": False,
            "reason": failure_reason or "max_retries_must_be_positive",
            "payload_bytes_target": target_bytes,
            "payload_bytes_actual": 0,
            "stegotext": None,
            "decode_ok": None,
            "ppl": None,
            "params_used": None,
            "latency_ms": 0,
            "attempt": 0,
        }

    last_rejected_stegotext: str | None = None
    for attempt in range(1, sample.max_retries + 1):
        # Stride well clear of the +1/+2 seed offsets run_comparison_frames and
        # _verified_frame apply, so retry prompts never collide with a sibling
        # carrier's. Attempt 1 keeps the caller's seed exactly.
        attempt_seed = sample.seed + (attempt - 1) * ATTEMPT_SEED_STRIDE
        prompt = _prompt_for_attempt(sample, attempt_seed)
        started = time.perf_counter()
        try:
            hide_resp = post_json(
                hide_url,
                {
                    "prompt": prompt,
                    "secret": sample.target_payload,
                    "complete_sent": True,
                    "threshold": sample.threshold,
                    "temperature": sample.temperature,
                    "temperature_alpha": sample.temperature_alpha,
                    "max_bpw": sample.max_bpw,
                    "max_new_tokens": sample.max_new_tokens,
                    "quality_max_words": sample.quality_max_words,
                    "quality_max_retries": sample.quality_max_retries,
                    "__timeout_seconds__": sample.request_timeout_seconds,
                },
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            partial_result = _partial_hide_result_from_error(
                exc=exc,
                target_payload=sample.target_payload,
                target_bytes=target_bytes,
                latency_ms=latency_ms,
                attempt=attempt,
            )
            if partial_result is not None:
                return partial_result
            failure_reason = f"hide_request_failed: {exc}"
            if is_retryable_hide_error(exc) and attempt < sample.max_retries:
                continue
            return {
                "accepted": False,
                "reason": failure_reason,
                "payload_bytes_target": target_bytes,
                "payload_bytes_actual": 0,
                "stegotext": None,
                "decode_ok": None,
                "ppl": None,
                "params_used": None,
                "latency_ms": 0,
                "attempt": attempt,
                "hide_request_debug": {
                    "prompt_chars": len(prompt),
                    "secret_bytes": target_bytes,
                    "corpus": sample.corpus,
                    "examples_in_prompt": min(len(sample.cover_texts), max(1, sample.n_cover)),
                    "seed": attempt_seed,
                    "n_cover": sample.n_cover,
                    "threshold": sample.threshold,
                    "temperature": sample.temperature,
                    "temperature_alpha": sample.temperature_alpha,
                    "max_bpw": sample.max_bpw,
                    "max_new_tokens": sample.max_new_tokens,
                    "quality_max_words": sample.quality_max_words,
                    "request_timeout_seconds": sample.request_timeout_seconds,
                },
            }

        latency_ms = int((time.perf_counter() - started) * 1000)
        payload_bytes_actual = int(hide_resp.get("payload_bytes") or 0)
        is_truncated = bool(hide_resp.get("is_truncated"))
        stegotext = hide_resp.get("stegotext")
        used_bits = hide_resp.get("used_bits")
        target_bits = hide_resp.get("target_bits")
        payload_bits_len = hide_resp.get("payload_bits")
        stego_token_ids = hide_resp.get("stego_token_ids")
        context_seed = hide_resp.get("context_seed")
        effective_prompt_hash = hide_resp.get("effective_prompt_hash")
        ppl = hide_resp.get("ppl")
        params_used = hide_resp.get("params_used")

        if is_truncated:
            if isinstance(stegotext, str) and stegotext:
                return _partial_hide_result(
                    target_payload=sample.target_payload,
                    target_bytes=target_bytes,
                    stegotext=stegotext,
                    used_bits=int(used_bits) if isinstance(used_bits, (int, float)) else 0,
                    target_bits=int(target_bits) if isinstance(target_bits, (int, float)) else None,
                    is_truncated=True,
                    ppl=ppl,
                    params_used=params_used,
                    latency_ms=latency_ms,
                    attempt=attempt,
                )
            failure_reason = "hide_truncated"
            continue
        if payload_bytes_actual != target_bytes:
            failure_reason = (
                f"payload_size_mismatch: expected={target_bytes}, got={payload_bytes_actual}"
            )
            continue
        if not isinstance(stegotext, str) or not stegotext:
            failure_reason = "missing_stegotext"
            continue
        if stegotext_has_prompt_leakage(stegotext):
            failure_reason = "prompt_leakage_detected"
            last_rejected_stegotext = stegotext
            continue

        decode_ok: bool | None = None
        if sample.do_reveal_check:
            try:
                reveal_resp = post_json(
                    reveal_url,
                    {
                        "prompt": prompt,
                        "stegotext": stegotext,
                        "stego_token_ids": stego_token_ids,
                        "context_seed": context_seed,
                        "effective_prompt_hash": effective_prompt_hash,
                        "payload_bits_len": payload_bits_len,
                        "threshold": sample.threshold,
                        "temperature": sample.temperature,
                        "temperature_alpha": sample.temperature_alpha,
                        "max_bpw": sample.max_bpw,
                        "__timeout_seconds__": sample.request_timeout_seconds,
                    },
                )
            except Exception as exc:
                failure_reason = f"reveal_request_failed: {exc}"
                return {
                    "accepted": False,
                    "reason": failure_reason,
                    "payload_bytes_target": target_bytes,
                    "payload_bytes_actual": payload_bytes_actual,
                    "stegotext": stegotext,
                    "decode_ok": None,
                    "ppl": float(ppl) if isinstance(ppl, (int, float)) else None,
                    "params_used": params_used if isinstance(params_used, dict) else None,
                    "latency_ms": latency_ms,
                    "attempt": attempt,
                    "hide_response_debug": {
                        "stego_token_ids": stego_token_ids,
                        "context_seed": context_seed,
                        "effective_prompt_hash": effective_prompt_hash,
                    },
                }
            decode_ok = bool(reveal_resp.get("decode_ok"))
            recovered_secret = reveal_resp.get("secret")
            if not decode_ok:
                failure_reason = "reveal_decode_failed"
                continue
            if recovered_secret != sample.target_payload:
                failure_reason = "reveal_payload_mismatch"
                continue

        return {
            "accepted": True,
            "reason": None,
            "payload_bytes_target": target_bytes,
            "payload_bytes_actual": payload_bytes_actual,
            "encoded_bits": int(used_bits) if isinstance(used_bits, (int, float)) else None,
            "target_bits": int(target_bits) if isinstance(target_bits, (int, float)) else None,
            "stegotext": stegotext,
            "decode_ok": decode_ok,
            "ppl": float(ppl) if isinstance(ppl, (int, float)) else None,
            "params_used": params_used if isinstance(params_used, dict) else None,
            "latency_ms": latency_ms,
            "attempt": attempt,
            "reveal_context": {
                "prompt": prompt,
                # The prompt seed that actually won; retries resample cover
                # sentences, so sample.seed alone no longer reconstructs it.
                "prompt_seed": attempt_seed,
                "context_seed": context_seed,
                "effective_prompt_hash": effective_prompt_hash,
                "threshold": sample.threshold,
                "temperature": sample.temperature,
                "temperature_alpha": sample.temperature_alpha,
                "max_bpw": sample.max_bpw,
            },
        }

    return {
        "accepted": False,
        "reason": failure_reason or "unknown_failure",
        "payload_bytes_target": target_bytes,
        "payload_bytes_actual": 0,
        # Kept for post-hoc failure analysis: the text that tripped the check is
        # the only evidence of *why* the baseline failed, and is otherwise lost.
        "stegotext": None,
        "rejected_stegotext": last_rejected_stegotext,
        "decode_ok": None,
        "ppl": None,
        "params_used": None,
        "latency_ms": 0,
        "attempt": sample.max_retries,
    }


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE))


def _utf8_prefix(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _verified_capacity_fields(result: dict[str, Any]) -> dict[str, int]:
    useful_bits = max(0, int(result.get("payload_bytes_actual") or 0) * 8)
    total_bits = max(useful_bits, int(result.get("encoded_bits") or useful_bits))
    return {
        "payload_bits_encoded": useful_bits,
        "protocol_overhead_bits": total_bits - useful_bits,
        "total_embedded_bits": total_bits,
    }


def _verified_frame(sample: ComparisonInput) -> tuple[dict[str, Any], str]:
    result = run_comparison_sample(replace(sample, use_capacity_probe=False))
    if result.get("accepted") and not result.get("partial") and result.get("decode_ok") is True:
        return result, sample.target_payload
    if not result.get("partial"):
        return result, ""
    actual_bytes = int(result.get("payload_bytes_actual") or 0)
    prefix = _utf8_prefix(sample.target_payload, actual_bytes)
    if not prefix or prefix == sample.target_payload:
        return result, ""
    retry = run_comparison_sample(
        replace(sample, target_payload=prefix, use_capacity_probe=False, seed=sample.seed + 1)
    )
    if retry.get("accepted") and not retry.get("partial") and retry.get("decode_ok") is True:
        return retry, prefix
    return retry, ""


def run_comparison_frames(
    sample: ComparisonInput, *, max_carriers: int = 8, max_total_words: int = 320
) -> dict[str, Any]:
    """Hide one payload across dynamically sized, independently verified ZLG carriers."""
    remaining = sample.target_payload
    frames: list[dict[str, Any]] = []
    total_words = 0
    for carrier_index in range(max(0, max_carriers)):
        if not remaining:
            break
        frame, consumed = _verified_frame(
            replace(sample, target_payload=remaining, seed=sample.seed + carrier_index * 2)
        )
        stegotext = str(frame.get("stegotext") or "")
        words = _word_count(stegotext)
        frame = {**frame, **_verified_capacity_fields(frame), "word_count": words}
        frame["payload_segment"] = consumed
        frames.append(frame)
        total_words += words
        if not consumed or total_words > max_total_words:
            break
        remaining = remaining[len(consumed) :]
    target_bits = len(sample.target_payload.encode("utf-8")) * 8
    useful_bits = (len(sample.target_payload.encode("utf-8")) - len(remaining.encode("utf-8"))) * 8
    total_bits = sum(int(frame["total_embedded_bits"]) for frame in frames)
    succeeded = not remaining and all(frame.get("decode_ok") is True for frame in frames)
    return {
        "accepted": succeeded,
        "reason": None if succeeded else "dynamic_carrier_budget_exhausted",
        "frames": frames,
        "carrier_count": len(frames),
        "word_count": total_words,
        "payload_bits_target": target_bits,
        "payload_bits_encoded": useful_bits,
        "protocol_overhead_bits": max(0, total_bits - useful_bits),
        "total_embedded_bits": total_bits,
        "remaining_payload": remaining,
        "target_payload": sample.target_payload,
        "decode_ok": succeeded,
    }


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
