"""Candidate generation, decoding evaluation, and sharpening collaboration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from workflows.contracts import PostAugmentation
from workflows.pipelines.stego_contextuality import contextuality_gate
from workflows.pipelines.stego_results import angle_summary

GenerateTexts = Callable[..., list[str]]
ReviseCandidate = Callable[..., str]
DecodeCandidate = Callable[..., tuple[int | None, int]]
EvaluateGroups = Callable[..., dict[str, Any]]


def _same_angle(lhs: dict[str, Any], rhs: dict[str, Any]) -> bool:
    return all(lhs.get(key) == rhs.get(key) for key in ("category", "tangent", "source_quote"))


def _distance_bucket(decoded_idx: int | None, selected_idx: Any) -> str:
    if not isinstance(decoded_idx, int) or not isinstance(selected_idx, int):
        return "unknown"
    distance = abs(decoded_idx - selected_idx)
    if distance == 0:
        return "exact"
    return "adjacent" if distance <= 2 else "far"


def _text_preview(text: str, max_len: int = 160) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= max_len else f"{clean[: max_len - 3]}..."


class StegoCandidateEngine:
    """Coordinates candidate work through injected pipeline adapter callbacks."""

    def __init__(
        self,
        *,
        generate_texts: GenerateTexts,
        revise_candidate: ReviseCandidate,
        decode_candidate: DecodeCandidate,
        evaluate_groups: EvaluateGroups | None = None,
    ) -> None:
        self.generate_texts = generate_texts
        self.revise_candidate = revise_candidate
        self.decode_candidate = decode_candidate
        self.evaluate_groups = evaluate_groups

    def generate_groups(
        self,
        *,
        samples: list[dict[str, Any]],
        post_augmentation: PostAugmentation,
        selected_angle: dict[str, Any],
        prompt_style: str,
        encode_run_id: str,
        llm_timings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for sample_index, sample in enumerate(samples):
            texts = self.generate_texts(
                sample=sample,
                comment_embedding=post_augmentation["commentEmbedding"],
                prompt_style=prompt_style,
                sample_index=sample_index,
                encode_run_id=encode_run_id,
                llm_timings=llm_timings,
            )
            groups.append(
                {
                    "category": sample.get("category"),
                    "source_quote": sample.get("source_quote"),
                    "tangent": sample.get("tangent"),
                    "prompt_style": prompt_style,
                    "texts": texts,
                }
            )
        return groups

    def sharpen(
        self,
        *,
        validation: dict[str, Any],
        encoded_results: list[dict[str, Any]],
        tangents_db: list[dict[str, Any]],
        selected_angle: dict[str, Any],
        post_augmentation: PostAugmentation,
        encode_run_id: str,
        llm_timings: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if self.evaluate_groups is None:
            raise RuntimeError("Candidate evaluation callback is required for sharpening")
        for revision_attempt, promising in enumerate(validation.get("promising_candidates") or [], start=1):
            group_index = int(promising.get("group_index", 0))
            source = encoded_results[group_index]
            failure_feedback = ", ".join(
                str(reason)
                for reason in (promising.get("rejection_reasons") or [])
                if str(reason).strip()
            ) or str(promising.get("distance_bucket") or "decode_or_quality_failure")
            text = self.revise_candidate(
                candidate_text=str(promising.get("text", "")),
                sample=source,
                comment_embedding=post_augmentation["commentEmbedding"],
                encode_run_id=encode_run_id,
                sample_index=group_index,
                llm_timings=llm_timings,
                failure_feedback=failure_feedback,
                revision_attempt=revision_attempt,
            )
            sharpened = {
                "category": source.get("category"),
                "source_quote": source.get("source_quote"),
                "tangent": source.get("tangent"),
                "lucid_intent": source.get("lucid_intent"),
                "prompt_style": "lucid_revision",
                "texts": [text],
                "generation_mode": "context_sharpen",
                "revision_attempt": revision_attempt,
                "failure_feedback": failure_feedback,
            }
            sharpen_validation = self.evaluate_groups(
                encoded_results=[sharpened],
                tangents_db=tangents_db,
                selected_angle=selected_angle,
                post_augmentation=post_augmentation,
                encode_run_id=encode_run_id,
            )
            if sharpen_validation.get("succeeded"):
                encoded_results.append(sharpened)
                return sharpen_validation.get("accepted_candidate") or {}, sharpen_validation
        return None

    def _evaluate_text(
        self,
        *,
        text: str,
        group: dict[str, Any],
        group_index: int,
        candidate_index: int,
        few_shots: list[dict[str, Any]],
        tangents_db: list[dict[str, Any]],
        selected_angle: dict[str, Any],
        post_augmentation: PostAugmentation,
    ) -> dict[str, Any]:
        strict_idx, strict_ms = self.decode_candidate(
            text=text, few_shots=few_shots, tangents_db=tangents_db, strict_mode=True
        )
        relaxed_idx, relaxed_ms = strict_idx, strict_ms
        if strict_idx is None:
            relaxed_idx, relaxed_ms = self.decode_candidate(
                text=text, few_shots=few_shots, tangents_db=tangents_db, strict_mode=False
            )
        decoded = (
            tangents_db[relaxed_idx]
            if isinstance(relaxed_idx, int) and 0 <= relaxed_idx < len(tangents_db)
            else None
        )
        gate = contextuality_gate(
            text,
            post_augmentation=post_augmentation,
            sample=group,
            selected_angle=selected_angle,
        )
        selected_idx = selected_angle.get("idx")
        exact_strict = strict_idx == selected_idx
        exact_relaxed = relaxed_idx == selected_idx
        bucket = _distance_bucket(relaxed_idx, selected_idx)
        reasons = list(gate["reasons"])
        if not exact_strict:
            reasons.append(
                "adjacent_angle_mismatch"
                if bucket == "adjacent"
                else ("weak_decoder_mode" if exact_relaxed else "decode_mismatch")
            )
        return {
            "group_index": group_index,
            "candidate_index": candidate_index,
            "text": text,
            "text_preview": _text_preview(text),
            "prompt_style": group.get("prompt_style"),
            "sample_category": group.get("category"),
            "sample_tangent": group.get("tangent"),
            "strict_decoded_index": strict_idx,
            "decoded_index": relaxed_idx,
            "decoded_angle": angle_summary(decoded),
            "distance_bucket": bucket,
            "strict_decode_ms": strict_ms,
            "relaxed_decode_ms": relaxed_ms,
            "context_gate": gate,
            "matches_selected_angle": exact_relaxed,
            "accepted": exact_strict and gate["passes"],
            "rejection_reasons": reasons,
        }

    def evaluate(
        self,
        *,
        encoded_results: list[dict[str, Any]],
        tangents_db: list[dict[str, Any]],
        selected_angle: dict[str, Any],
        post_augmentation: PostAugmentation,
    ) -> dict[str, Any]:
        evaluations: list[dict[str, Any]] = []
        for group_index, group in enumerate(encoded_results):
            texts = group.get("texts", [])
            if not isinstance(texts, list):
                continue
            few_shots = [item for idx, item in enumerate(encoded_results) if idx != group_index]
            for candidate_index, text in enumerate(texts):
                if isinstance(text, str) and text.strip():
                    evaluations.append(
                        self._evaluate_text(
                            text=text,
                            group=group,
                            group_index=group_index,
                            candidate_index=candidate_index,
                            few_shots=few_shots,
                            tangents_db=tangents_db,
                            selected_angle=selected_angle,
                            post_augmentation=post_augmentation,
                        )
                    )
        evaluations.sort(
            key=lambda item: (
                0 if item["accepted"] else 1,
                0 if item["context_gate"]["passes"] else 1,
                0
                if item["distance_bucket"] == "exact"
                else (1 if item["distance_bucket"] == "adjacent" else 2),
                len(item["context_gate"]["unsupported_tokens"]),
                item["group_index"],
                item["candidate_index"],
            )
        )
        accepted = next((item for item in evaluations if item["accepted"]), None)
        promising = [
            item
            for item in evaluations
            if item["distance_bucket"] in {"exact", "adjacent"} or item["matches_selected_angle"]
        ][:3]
        return {
            "succeeded": accepted is not None,
            "accepted_candidate": accepted,
            "promising_candidate": promising[0] if promising else None,
            "promising_candidates": promising,
            "validationDetails": {
                "selected_angle": angle_summary(selected_angle),
                "candidates": [
                    {
                        key: item[key]
                        for key in (
                            "group_index",
                            "candidate_index",
                            "decoded_index",
                            "strict_decoded_index",
                            "decoded_angle",
                            "distance_bucket",
                            "matches_selected_angle",
                            "accepted",
                            "rejection_reasons",
                            "text_preview",
                            "context_gate",
                        )
                    }
                    for item in evaluations
                ],
            },
        }
