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
from workflows.adapters.llm import LLMAdapter, strip_redacted_thinking
from workflows.utils.workflow_llm_prompts import get_prompts

logger = logging.getLogger(__name__)

# Must match ``gpt-oss`` node + stego encoder (``stego.STEGO_LLM_MODEL``).
# DECODE_LLM_MODEL = "openai/gpt-oss-20b"
DECODE_LLM_MODEL = "qwen/qwen3.5-9b"
# HTTP Request body ``n`` in Decode workflow.
DECODE_SEMANTIC_TOP_N = 20
# G Decode node: retryOnFail / maxTries.
DECODE_LLM_MAX_TRIES = 5
_DECODE_LOG_BASE: dict[str, str] = {
    "tag": "decode",
    "component": "DecodePipeline",
    "log_domain": "stego",
    "log_op": "decode",
}


def _decode_log_extra(**fields: Any) -> dict[str, Any]:
    merged: dict[str, Any] = dict(_DECODE_LOG_BASE)
    merged.update(fields)
    return merged
# Allow room for inline thinking tags before idx: while still bounding decode length.
_DECODE_MAX_TOKENS = 128


def _angle_signature(angle: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(angle.get("category", "")),
        str(angle.get("source_quote", "")),
        str(angle.get("tangent", "")),
    )


def _labeled_angle_candidates(
    top_candidates: List[Dict[str, Any]], angles: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Angles shown to the decode LLM with canonical global ``idx`` (matches ``angles`` index)."""
    out: List[Dict[str, Any]] = []
    for c in top_candidates:
        i = c.get("index")
        if isinstance(i, int) and 0 <= i < len(angles):
            merged = dict(angles[i])
            merged["idx"] = i
            out.append(merged)
    return out


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
            logger.warning(
                "No angles provided; cannot decode",
                extra=_decode_log_extra(log_area="input"),
            )
            return None

        try:
            logger.info(
                "text_len=%s angles=%s few_shots=%s",
                len(stego_text or ""),
                len(angles),
                len(few_shots or []),
                extra=_decode_log_extra(log_area="start"),
            )

            search_result = self.backend.semantic_search(
                text=stego_text,
                objects=angles,
                n=DECODE_SEMANTIC_TOP_N,
            )

            results = search_result.get("results", [])
            if not isinstance(results, list) or not results:
                logger.warning(
                    "No semantic matches returned",
                    extra=_decode_log_extra(log_area="semantic", semantic_event="no_matches"),
                )
                return None

            lookup: Dict[Tuple[str, str, str], List[int]] = {}
            for idx, angle in enumerate(angles):
                lookup.setdefault(_angle_signature(angle), []).append(idx)

            top_candidates: List[Dict[str, Any]] = []
            unmapped_semantic = 0
            for rank, result in enumerate(results[:DECODE_SEMANTIC_TOP_N], start=1):
                obj = result.get("object", {})
                if not isinstance(obj, dict):
                    unmapped_semantic += 1
                    continue
                mapped_idx = self._find_angle_index(obj, lookup)
                if mapped_idx is None:
                    unmapped_semantic += 1
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
                    "No candidates mapped to source angles",
                    extra=_decode_log_extra(
                        log_area="semantic", semantic_event="no_candidate_map"
                    ),
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

            labeled = _labeled_angle_candidates(top_candidates, angles)
            system_candidates_text = json.dumps(
                labeled,
                ensure_ascii=False,
                indent=2,
            )
            allowed_indices = {c["index"] for c in top_candidates}
            allowed_sorted = ",".join(str(x) for x in sorted(allowed_indices))
            logger.info(
                "decode_semantic_candidate_diagnostics",
                extra=_decode_log_extra(
                    log_area="semantic",
                    semantic_event="candidate_diagnostics",
                    results_returned=len(results),
                    results_scanned=min(len(results), DECODE_SEMANTIC_TOP_N),
                    top_candidates_count=len(top_candidates),
                    unmapped_semantic_count=unmapped_semantic,
                    labeled_for_prompt_count=len(labeled),
                    allowed_indices=allowed_sorted,
                    top1_index=top_candidates[0]["index"],
                ),
            )

            dec = get_prompts().stego_decode
            prompt = dec.user_template.format(
                few_shots=few_shot_text,
                stego_text=stego_text,
            )
            system_message = dec.system_template.format(
                angle_count=len(angles),
                candidates_json=system_candidates_text,
            )
            logger.info(
                "%s",
                system_message,
                extra=_decode_log_extra(log_area="prompt", prompt_role="system"),
            )
            logger.info(
                "%s",
                prompt,
                extra=_decode_log_extra(log_area="prompt", prompt_role="user"),
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
                        "attempt %s/%s failed: %s",
                        attempt,
                        DECODE_LLM_MAX_TRIES,
                        exc,
                        extra=_decode_log_extra(log_area="llm", llm_event="attempt_failed"),
                    )
            if response is None:
                logger.error(
                    "exhausted retries",
                    exc_info=last_exc,
                    extra=_decode_log_extra(log_area="llm", llm_event="exhausted"),
                )
                return None

            logger.info(
                "%s",
                response.strip(),
                extra=_decode_log_extra(log_area="llm", llm_stage="raw"),
            )

            stripped_for_parse = strip_redacted_thinking(response or "")
            logger.info(
                "decode_strip_probe",
                extra=_decode_log_extra(
                    log_area="llm",
                    llm_event="strip_probe",
                    response_chars=len(response or ""),
                    stripped_chars=len(stripped_for_parse),
                    decode_max_tokens=_DECODE_MAX_TOKENS,
                ),
            )
            if (response or "").strip() and not stripped_for_parse.strip():
                logger.warning(
                    "decode_strip_removed_all_parseable_content",
                    extra=_decode_log_extra(
                        log_area="llm",
                        llm_event="strip_empty",
                        response_chars=len(response or ""),
                    ),
                )

            picked, how = _extract_decode_index(
                response, allowed_indices, top_candidates
            )
            if picked is not None and how != "rank_fallback":
                logger.info(
                    "index=%s via=%s",
                    picked,
                    how,
                    extra=_decode_log_extra(
                        log_area="llm",
                        llm_event="index",
                        extract_how=how,
                    ),
                )
                return picked
            if picked is not None and how == "rank_fallback":
                logger.warning(
                    "rank fallback -> index=%s",
                    picked,
                    extra=_decode_log_extra(
                        log_area="llm",
                        llm_event="rank_fallback",
                        extract_how=how,
                    ),
                )
                return picked

            semantic_fallback = top_candidates[0]["index"]
            logger.warning(
                "Unparsed LLM response '%s'; using top semantic index=%s",
                response.strip(),
                semantic_fallback,
                extra=_decode_log_extra(
                    log_area="fallback",
                    extract_how=how,
                    semantic_fallback_index=semantic_fallback,
                ),
            )
            return semantic_fallback

        except Exception:
            logger.exception(
                "Failed to decode stego text",
                extra=_decode_log_extra(log_area="error"),
            )
            return None
