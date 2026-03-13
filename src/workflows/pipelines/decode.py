"""Decode steganographic text back to angle index."""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config

logger = logging.getLogger(__name__)


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
        self.config = get_config()

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

            top_n = min(5, len(angles))
            search_result = self.backend.semantic_search(
                text=stego_text,
                objects=angles,
                n=top_n,
            )

            results = search_result.get("results", [])
            if not isinstance(results, list) or not results:
                logger.warning("[DECODE][SEMANTIC] No semantic matches returned")
                return None

            lookup: Dict[Tuple[str, str, str], List[int]] = {}
            for idx, angle in enumerate(angles):
                lookup.setdefault(_angle_signature(angle), []).append(idx)

            top_candidates: List[Dict[str, Any]] = []
            for rank, result in enumerate(results[:top_n], start=1):
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
            if few_shots:
                few_shot_examples = []
                for shot in few_shots[:3]:
                    text_list = shot.get("texts", []) if isinstance(shot, dict) else []
                    if isinstance(text_list, list) and text_list:
                        first = text_list[0]
                        if isinstance(first, str) and first.strip():
                            few_shot_examples.append(first.strip())
                if few_shot_examples:
                    few_shot_text = "\n\nFew-shot examples:\n" + "\n".join(
                        f"- {example}" for example in few_shot_examples
                    )

            candidate_lines = "\n".join(
                f"- index={c['index']} | rank={c['rank']} | score={c.get('score')} | tangent={c['tangent']}"
                for c in top_candidates
            )
            allowed_indices = {c["index"] for c in top_candidates}

            prompt = (
                "Given the steganographic text below, pick the best matching angle index.\n\n"
                f"Stego text:\n{stego_text}\n\n"
                "Top semantic candidates (global angle indices):\n"
                f"{candidate_lines}"
                f"{few_shot_text}\n\n"
                "Return ONLY one integer index chosen from the listed indices."
            )
            system_message = (
                "You are a steganographic decoder. Return exactly one integer index."
            )

            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=self.config.model,
                provider="lm_studio",
                temperature=0.0,
            )

            numbers = [int(x) for x in re.findall(r"\d+", response.strip())]
            for number in numbers:
                if number in allowed_indices:
                    logger.info("[DECODE][LLM] Selected index=%s", number)
                    return number

            # Rank-based fallback: handle LLM responses like "1".."5" (candidate rank).
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
