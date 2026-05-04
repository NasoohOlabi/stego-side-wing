"""Decode steganographic text back to angle index.

Parity with n8n workflow ``workflows/tWT6U8IK_9oUBlJMRl0oa.json`` (Decode):
HTTP semantic_search with n=20, then G Decode agent with model qwen3.5-9b
(``qwen/qwen3.5-9b`` via LM Studio / OpenAI-compatible), retries max 5.
"""

import json
import re
from typing import Any

from loguru import logger

from infrastructure.config import (
    get_workflow_decode_llm_max_tries,
    get_workflow_decode_semantic_top_n,
    get_workflow_stego_llm_temperature,
    resolve_workflow_llm_provider_and_model,
)
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter, strip_redacted_thinking
from workflows.utils.workflow_llm_prompts import get_prompts

# Must match sender stego encoder model (``stego.STEGO_LLM_MODEL``).
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
def _angle_signature(angle: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(angle.get("category", "")),
        str(angle.get("source_quote", "")),
        str(angle.get("tangent", "")),
    )


def _labeled_angle_candidates(
    top_candidates: list[dict[str, Any]], angles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Angles shown to the decode LLM with canonical global ``idx`` (matches ``angles`` index)."""
    out: list[dict[str, Any]] = []
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


def _try_labeled_or_json_idx(raw: str, allowed: set[int]) -> tuple[int | None, str]:
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


def _try_last_line_digits(raw: str, allowed: set[int]) -> tuple[int | None, str]:
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
    top_candidates: list[dict[str, Any]],
) -> tuple[int | None, str]:
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


def _is_strict_decode_success_mode(mode: str) -> bool:
    return mode in {"json_idx", "labeled", "last_line"}


class DecodePipeline:
    """Runs semantic shortlist + LLM decode to map stego text to a tangent index."""

    def __init__(self) -> None:
        self.backend = BackendAPIAdapter()
        self.llm = LLMAdapter()
        self._log = logger.bind(**_DECODE_LOG_BASE)

    def _find_angle_index(
        self,
        target: dict[str, Any],
        lookup: dict[tuple[str, str, str], list[int]],
    ) -> int | None:
        indices = lookup.get(_angle_signature(target), [])
        return indices[0] if indices else None

    def decode(
        self,
        stego_text: str,
        angles: list[dict[str, Any]],
        few_shots: list[dict[str, Any]] | None = None,
        base_url: str | None = None,
        strict_mode: bool = False,
    ) -> int | None:
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

        if not hasattr(self, "_log"):
            object.__setattr__(self, "_log", logger.bind(**_DECODE_LOG_BASE))

        if not angles:
            self._log.warning("decode_no_angles", log_area="input")
            return None

        try:
            self._log.info(
                "decode_start",
                log_area="start",
                text_len=len(stego_text or ""),
                angles_count=len(angles),
                few_shots_count=len(few_shots or []),
            )

            semantic_top_n = get_workflow_decode_semantic_top_n()
            search_result = self.backend.semantic_search(
                text=stego_text,
                objects=angles,
                n=semantic_top_n,
            )

            results = search_result.get("results", [])
            if not isinstance(results, list) or not results:
                self._log.warning(
                    "decode_semantic_no_matches",
                    log_area="semantic",
                    semantic_event="no_matches",
                )
                return None

            lookup: dict[tuple[str, str, str], list[int]] = {}
            for idx, angle in enumerate(angles):
                lookup.setdefault(_angle_signature(angle), []).append(idx)

            top_candidates: list[dict[str, Any]] = []
            unmapped_semantic = 0
            for rank, result in enumerate(results[:semantic_top_n], start=1):
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
                self._log.warning(
                    "decode_semantic_no_candidate_map",
                    log_area="semantic",
                    semantic_event="no_candidate_map",
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
            self._log.info(
                "decode_semantic_candidate_diagnostics",
                log_area="semantic",
                semantic_event="candidate_diagnostics",
                results_returned=len(results),
                results_scanned=min(len(results), semantic_top_n),
                top_candidates_count=len(top_candidates),
                unmapped_semantic_count=unmapped_semantic,
                labeled_for_prompt_count=len(labeled),
                allowed_indices=allowed_sorted,
                top1_index=top_candidates[0]["index"],
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
            self._log.info(
                "decode_prompt_system",
                log_area="prompt",
                prompt_role="system",
                prompt_body=system_message,
            )
            self._log.info(
                "decode_prompt_user",
                log_area="prompt",
                prompt_role="user",
                prompt_body=prompt,
            )

            response: str | None = None
            last_exc: BaseException | None = None
            provider, model = resolve_workflow_llm_provider_and_model(DECODE_LLM_MODEL)
            max_tries = get_workflow_decode_llm_max_tries()
            for attempt in range(1, max_tries + 1):
                try:
                    response = self.llm.call_llm(
                        prompt=prompt,
                        system_message=system_message,
                        model=model,
                        provider=provider,
                        temperature=get_workflow_stego_llm_temperature(),
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    self._log.warning(
                        "decode_llm_attempt_failed",
                        log_area="llm",
                        llm_event="attempt_failed",
                        attempt=attempt,
                        max_tries=max_tries,
                        error=str(exc),
                    )
            if response is None:
                if last_exc is not None:
                    self._log.opt(exception=last_exc).error(
                        "decode_llm_retries_exhausted",
                        log_area="llm",
                        llm_event="exhausted",
                    )
                else:
                    self._log.error(
                        "decode_llm_retries_exhausted",
                        log_area="llm",
                        llm_event="exhausted",
                    )
                return None

            self._log.info(
                "decode_llm_raw_response",
                log_area="llm",
                llm_stage="raw",
                response_body=response.strip() if response else "",
            )

            stripped_for_parse = strip_redacted_thinking(response or "")
            self._log.info(
                "decode_strip_probe",
                log_area="llm",
                llm_event="strip_probe",
                response_chars=len(response or ""),
                stripped_chars=len(stripped_for_parse),
            )
            if (response or "").strip() and not stripped_for_parse.strip():
                self._log.warning(
                    "decode_strip_removed_all_parseable_content",
                    log_area="llm",
                    llm_event="strip_empty",
                    response_chars=len(response or ""),
                )

            picked, how = _extract_decode_index(response, allowed_indices, top_candidates)
            if strict_mode:
                if picked is None:
                    self._log.warning(
                        "decode_strict_parse_failed",
                        log_area="strict",
                        strict_mode=True,
                        extract_how=how,
                    )
                    return None
                if not _is_strict_decode_success_mode(how):
                    self._log.warning(
                        "decode_strict_rejected_fallback_mode",
                        log_area="strict",
                        strict_mode=True,
                        decoded_index=picked,
                        extract_how=how,
                    )
                    return None
                self._log.info(
                    "decode_strict_index_resolved",
                    log_area="strict",
                    strict_mode=True,
                    decoded_index=picked,
                    extract_how=how,
                )
                return picked
            if picked is not None and how != "rank_fallback":
                self._log.info(
                    "decode_index_resolved",
                    log_area="llm",
                    llm_event="index",
                    decoded_index=picked,
                    extract_how=how,
                )
                return picked
            if picked is not None and how == "rank_fallback":
                self._log.warning(
                    "decode_rank_fallback",
                    log_area="llm",
                    llm_event="rank_fallback",
                    decoded_index=picked,
                    extract_how=how,
                )
                return picked

            semantic_fallback = top_candidates[0]["index"]
            self._log.warning(
                "decode_unparsed_llm_fallback",
                log_area="fallback",
                extract_how=how,
                semantic_fallback_index=semantic_fallback,
                response_preview=(response or "").strip()[:500],
            )
            return semantic_fallback

        except Exception:
            self._log.exception("decode_failed", log_area="error")
            return None
