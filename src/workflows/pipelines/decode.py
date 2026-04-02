"""Decode steganographic text back to angle index.

Parity with n8n workflow ``workflows/tWT6U8IK_9oUBlJMRl0oa.json`` (Decode):
HTTP semantic_search with n=20, then G Decode agent with model gpt-oss-20b
(``openai/gpt-oss-20b`` via LM Studio / OpenAI-compatible), retries max 5.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter

logger = logging.getLogger(__name__)

# Must match ``gpt-oss`` node + stego encoder (``stego.STEGO_LLM_MODEL``).
# DECODE_LLM_MODEL = "openai/gpt-oss-20b"
DECODE_LLM_MODEL = "qwen/qwen3.5-9b"
# HTTP Request body ``n`` in Decode workflow.
DECODE_SEMANTIC_TOP_N = 20
# G Decode node: retryOnFail / maxTries.
DECODE_LLM_MAX_TRIES = 5
_DECODE_PROMPT_LOG_EXTRA: dict[str, str] = {
    "tag": "decode",
    "component": "DecodePipeline",
}
# Cap decode completion so the model cannot emit long reasoning (LM Studio / local models).
_DECODE_MAX_TOKENS = 48


def _angle_signature(angle: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(angle.get("category", "")),
        str(angle.get("source_quote", "")),
        str(angle.get("tangent", "")),
    )


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            s = "\n".join(inner).strip()
    return s


def _try_labeled_or_json_idx(raw: str, allowed: set[int]) -> Tuple[Optional[int], str]:
    m_json = re.search(r'"idx"\s*:\s*(\d+)', raw)
    if m_json:
        n = int(m_json.group(1))
        if n in allowed:
            return n, "json_idx"
    for pat in (
        r"(?:^|\n)\s*idx\s*[:=]\s*(\d+)",
        r"(?:^|\n)\s*index\s*[:=]\s*(\d+)",
        r"(?:answer|output)\s*[:=]\s*(\d+)",
    ):
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if n in allowed:
                return n, "labeled"
    return None, "none"


def _try_last_line_digits(raw: str, allowed: set[int]) -> Tuple[Optional[int], str]:
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return None, "none"
    last = lines[-1]
    if re.fullmatch(r"\d+", last):
        n = int(last)
        if n in allowed:
            return n, "last_line"
    return None, "none"


def _extract_decode_index(
    response: str,
    allowed_indices: set[int],
    top_candidates: List[Dict[str, Any]],
) -> Tuple[Optional[int], str]:
    """Prefer structured / final-line digits over the first number in verbose prose."""
    raw = _strip_code_fence(response)
    for fn in (_try_labeled_or_json_idx, _try_last_line_digits):
        n, how = fn(raw, allowed_indices)
        if n is not None:
            return n, how
    numbers = [int(x) for x in re.findall(r"\d+", raw)]
    for number in reversed(numbers):
        if number in allowed_indices:
            return number, "last_allowed_digit"
    for number in numbers:
        if number in allowed_indices:
            return number, "first_allowed_digit"
    for number in reversed(numbers):
        if 1 <= number <= len(top_candidates):
            return top_candidates[number - 1]["index"], "rank_fallback"
    return None, "none"


class DecodePipeline:
    """Pipeline for decoding stego text to angle index."""

    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.llm = LLMAdapter()

    def _find_angle_index(
        self,
        target: Dict[str, Any],
        lookup: Dict[Tuple[str, str, str], List[int]],
    ) -> Optional[int]:
        indices = lookup.get(_angle_signature(target), [])
        return indices[0] if indices else None

    def decode(
        self,
        stego_text: str,
        angles: List[Dict[str, Any]],
        few_shots: Optional[List[Dict[str, Any]]] = None,
        base_url: Optional[str] = None,
    ) -> Optional[int]:
        """
        Decode stego text to angle index.

        Args:
            stego_text: Steganographic text to decode
            angles: List of angle dictionaries
            few_shots: Optional few-shot examples
            base_url: Base URL for API calls

        Returns:
            Decoded angle index (0-based) or None if decoding fails
        """
        del base_url  # Kept for backward compatibility in callers/tests.

        if not angles:
            logger.warning("[DECODE][INPUT] No angles provided; cannot decode")
            return None

        try:
            logger.info(
                "[DECODE][START] text_len=%s angles=%s few_shots=%s",
                len(stego_text or ""),
                len(angles),
                len(few_shots or []),
            )

            search_result = self.backend.semantic_search(
                text=stego_text,
                objects=angles,
                n=DECODE_SEMANTIC_TOP_N,
            )

            results = search_result.get("results", [])
            if not isinstance(results, list) or not results:
                logger.warning("[DECODE][SEMANTIC] No semantic matches returned")
                return None

            lookup: Dict[Tuple[str, str, str], List[int]] = {}
            for idx, angle in enumerate(angles):
                lookup.setdefault(_angle_signature(angle), []).append(idx)

            top_candidates: List[Dict[str, Any]] = []
            for rank, result in enumerate(results[:DECODE_SEMANTIC_TOP_N], start=1):
                obj = result.get("object", {})
                if not isinstance(obj, dict):
                    continue
                mapped_idx = self._find_angle_index(obj, lookup)
                if mapped_idx is None:
                    continue
                top_candidates.append(
                    {
                        "rank": rank,
                        "index": mapped_idx,
                        "score": result.get("score"),
                        "tangent": str(obj.get("tangent", ""))[:140],
                    }
                )

            if not top_candidates:
                logger.warning(
                    "[DECODE][SEMANTIC] No candidates mapped to source angles"
                )
                return None

            # Build few-shot prompt section.
            few_shot_text = ""
            if isinstance(few_shots, list):
                few_shot_text = json.dumps(
                    few_shots,
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                few_shot_text = "[]"

            semantic_objects = [
                result.get("object")
                for result in results[:DECODE_SEMANTIC_TOP_N]
                if isinstance(result, dict) and isinstance(result.get("object"), dict)
            ]
            system_candidates_text = json.dumps(
                semantic_objects,
                ensure_ascii=False,
                indent=2,
            )
            allowed_indices = {c["index"] for c in top_candidates}

            prompt = (
                "### FEW-SHOT EXAMPLES:\n"
                f"{few_shot_text}\n\n"
                "### INPUT TEXT:\n"
                f"{stego_text}\n\n"
                "Reply with exactly one line in this form and nothing else:\n"
                "idx: <integer>\n"
                "where <integer> is the 0-based index of the matching angle from the list in the system message."
            )
            system_message = (
                "You choose exactly one angle from the JSON list below that best matches the INPUT TEXT.\n"
                "Output format (mandatory): a single line only, exactly: idx: N\n"
                "N must be an integer with 0 <= N < "
                f"{len(angles)}. Do not explain, apologize, analyze, or add any other text or numbers.\n\n"
                f"{system_candidates_text}"
            )
            logger.info(
                "[DECODE][PROMPT][SYSTEM]\n%s",
                system_message,
                extra=_DECODE_PROMPT_LOG_EXTRA,
            )
            logger.info(
                "[DECODE][PROMPT][USER]\n%s",
                prompt,
                extra=_DECODE_PROMPT_LOG_EXTRA,
            )

            response: Optional[str] = None
            last_exc: Optional[BaseException] = None
            for attempt in range(1, DECODE_LLM_MAX_TRIES + 1):
                try:
                    response = self.llm.call_llm(
                        prompt=prompt,
                        system_message=system_message,
                        model=DECODE_LLM_MODEL,
                        provider="lm_studio",
                        temperature=0.0,
                        max_tokens=_DECODE_MAX_TOKENS,
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "[DECODE][LLM] attempt %s/%s failed: %s",
                        attempt,
                        DECODE_LLM_MAX_TRIES,
                        exc,
                    )
            if response is None:
                logger.error("[DECODE][LLM] exhausted retries", exc_info=last_exc)
                return None

            logger.info("[DECODE][LLM][RAW] %s", response.strip())

            picked, how = _extract_decode_index(
                response, allowed_indices, top_candidates
            )
            if picked is not None and how != "rank_fallback":
                logger.info("[DECODE][LLM] index=%s via=%s", picked, how)
                return picked
            if picked is not None and how == "rank_fallback":
                logger.warning(
                    "[DECODE][LLM] rank fallback -> index=%s",
                    picked,
                )
                return picked

            semantic_fallback = top_candidates[0]["index"]
            logger.warning(
                "[DECODE][FALLBACK] Unparsed LLM response '%s'; using top semantic index=%s",
                response.strip(),
                semantic_fallback,
            )
            return semantic_fallback

        except Exception:
            logger.exception("[DECODE][ERROR] Failed to decode stego text")
            return None
