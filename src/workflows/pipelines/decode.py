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
DECODE_LLM_MODEL = "openai/gpt-oss-20b"
# HTTP Request body ``n`` in Decode workflow.
DECODE_SEMANTIC_TOP_N = 20
# G Decode node: retryOnFail / maxTries.
DECODE_LLM_MAX_TRIES = 5


def _angle_signature(angle: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(angle.get("category", "")),
        str(angle.get("source_quote", "")),
        str(angle.get("tangent", "")),
    )


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
                logger.warning("[DECODE][SEMANTIC] No candidates mapped to source angles")
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
                "### OUTPUT (idx only):"
            )
            system_message = (
                "You Job is to identify the angle/tangent from given texts from the given predefined list\n\n"
                "return the angle/tangent index only! (OUTPUT: MUST BE A SINGLE NUMBER)\n\n"
                "the index must be within the following list size, please make sure to return one number "
                f"thats smaller than {len(angles)} and larger than 0 (0 <= x < {len(angles)})!\n\n\n"
                f"{system_candidates_text}"
            )
            logger.info("[DECODE][PROMPT][SYSTEM]\n%s", system_message)
            logger.info("[DECODE][PROMPT][USER]\n%s", prompt)

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

            numbers = [int(x) for x in re.findall(r"\d+", response.strip())]
            for number in numbers:
                if number in allowed_indices:
                    logger.info("[DECODE][LLM] Selected index=%s", number)
                    return number

            # Rank-based fallback: LLM returns 1-based rank within semantic shortlist.
            for number in numbers:
                if 1 <= number <= len(top_candidates):
                    ranked_idx = top_candidates[number - 1]["index"]
                    logger.warning(
                        "[DECODE][LLM] Interpreting rank=%s as index=%s fallback",
                        number,
                        ranked_idx,
                    )
                    return ranked_idx

            semantic_fallback = top_candidates[0]["index"]
            logger.warning(
                "[DECODE][FALLBACK] Invalid LLM response '%s'; using top semantic index=%s",
                response.strip(),
                semantic_fallback,
            )
            return semantic_fallback

        except Exception:
            logger.exception("[DECODE][ERROR] Failed to decode stego text")
            return None
