"""Encode result-dict assembly, shared by StegoPipeline's payload and diagnostic entry points.

Split out of ``stego.py`` (plan step 3.2; the functions here were first extracted as pure
functions in phase 3.1 of the maintainability refactor). ``@validate_call`` sits only on
``decoded_indices``/``candidate_validation_audit``: the other functions take
``post_augmentation``/``sender_audit`` typed as ``PostAugmentation``/``SenderAudit``, and
strict validation against those TypedDicts breaks the test suite's established mocking
convention of building deliberately partial fixtures for those two -- see
``docs/development/refactor-baseline.md`` for the investigation.
"""

from typing import Any

from pydantic import validate_call

from workflows.contracts import PostAugmentation, SenderAudit


def angle_summary(angle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(angle, dict):
        return None
    return {
        "idx": angle.get("idx"),
        "category": angle.get("category"),
        "tangent": angle.get("tangent"),
        "source_quote": angle.get("source_quote"),
        "source_document": angle.get("source_document"),
    }


@validate_call
def decoded_indices(validation_details: dict[str, Any]) -> list[Any]:
    """Decoded angle index per evaluated candidate, in evaluation order."""
    candidates = validation_details.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    return [item.get("decoded_index") for item in candidates]


@validate_call
def candidate_validation_audit(
    accepted_candidate: dict[str, Any], *, acceptance_source: str
) -> dict[str, Any]:
    """Sender-audit record describing which candidate was accepted and how."""
    return {
        "acceptance_source": acceptance_source,
        "group_index": accepted_candidate.get("group_index"),
        "candidate_index": accepted_candidate.get("candidate_index"),
        "decoded_index": accepted_candidate.get("decoded_index"),
        "strict_decoded_index": accepted_candidate.get("strict_decoded_index"),
    }


def _candidate_failure_kind(candidate: dict[str, Any]) -> str:
    if candidate.get("decoded_index") is None:
        return "no_decode"
    reasons = candidate.get("rejection_reasons", [])
    if any(reason in {"no_context_overlap", "unsupported_topic_drift", "weak_selected_angle_grounding"} for reason in reasons):
        return "weak_thread_grounding"
    if any(reason in {"decode_mismatch", "adjacent_angle_mismatch", "weak_decoder_mode"} for reason in reasons):
        return "wrong_angle_decode"
    return "quality_gate_violation"


@validate_call
def failure_taxonomy(validation_details: dict[str, Any]) -> dict[str, int]:
    """Count every failed natural candidate using LUCID's stable diagnostic taxonomy."""
    counts: dict[str, int] = {
        "no_decode": 0,
        "wrong_angle_decode": 0,
        "weak_thread_grounding": 0,
        "quality_gate_violation": 0,
    }
    candidates = validation_details.get("candidates", [])
    if not isinstance(candidates, list):
        return counts
    for candidate in candidates:
        if isinstance(candidate, dict) and not candidate.get("accepted"):
            kind = _candidate_failure_kind(candidate)
            counts[kind] += 1
    return counts


def encode_success_result(
    *,
    stego_text: str,
    post: dict[str, Any],
    selected_angle: dict[str, Any],
    selected_idx: Any,
    retry_count: int,
    tag: str | None,
    sender_audit: SenderAudit,
    post_augmentation: PostAugmentation,
    encoded_results: list[dict[str, Any]],
    validation_details: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Successful encode artifact, shared by the payload and diagnostic entry points.

    ``extra`` carries the diagnostic-only fields the binary-selection-bits path adds.
    """
    result: dict[str, Any] = {
        "stego_text": stego_text,
        "post": post,
        "selected_angle": selected_angle,
        "angle_index": selected_idx,
        "succeeded": True,
        "retry_count": retry_count,
        "tag": tag,
        "sender_audit": sender_audit,
        "embedding": post_augmentation,
        "encoded_samples": encoded_results,
        "decoded_indices": decoded_indices(validation_details or {}),
        "validation_details": validation_details,
    }
    if extra:
        result.update(extra)
    return result


def encode_failure_result(
    *,
    stego_text: str,
    post: dict[str, Any],
    selected_angle: dict[str, Any],
    selected_idx: Any,
    retry_count: int,
    tag: str | None,
    sender_audit: SenderAudit,
    post_augmentation: PostAugmentation,
    encoded_results: list[dict[str, Any]],
    validation_details: dict[str, Any],
    error_details: dict[str, Any],
    breakdown: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Result for an encode whose retries were exhausted without a decodable candidate.

    Both entry points report the same ``error``; they differ only in the reason text they
    put in ``error_details`` and in the trailing fields they add. Key insertion order
    mirrors what each path emitted before, so serialized artifacts are unchanged.
    """
    result: dict[str, Any] = {
        "stego_text": stego_text,
        "post": post,
        "selected_angle": selected_angle,
        "angle_index": selected_idx,
        "succeeded": False,
        "retry_count": retry_count,
        "tag": tag,
        "sender_audit": sender_audit,
        "error": "Decoding validation failed",
        "error_details": error_details,
    }
    if breakdown is not None:
        result["breakdown"] = breakdown
    result["validation_details"] = validation_details
    result["failure_taxonomy"] = failure_taxonomy(validation_details)
    result["embedding"] = post_augmentation
    result["encoded_samples"] = encoded_results
    if extra:
        result.update(extra)
    return result


def encode_exception_result(
    *,
    exc: Exception,
    post: dict[str, Any],
    selected_angle: dict[str, Any],
    selected_idx: Any,
    retry_count: int,
    tag: str | None,
    sender_audit: SenderAudit,
    post_augmentation: PostAugmentation,
    reason: str,
    breakdown: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Result for an encode attempt that raised after its retries were exhausted."""
    result: dict[str, Any] = {
        "stego_text": "",
        "post": post,
        "selected_angle": selected_angle,
        "angle_index": selected_idx,
        "succeeded": False,
        "retry_count": retry_count,
        "tag": tag,
        "sender_audit": sender_audit,
        "error": str(exc),
        "error_details": {
            "reason": reason,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "selected_angle": angle_summary(selected_angle),
        },
    }
    if breakdown is not None:
        result["breakdown"] = breakdown
    result["embedding"] = post_augmentation
    if extra:
        result.update(extra)
    return result


def diagnostic_bits_fields(bits: str, post_augmentation: PostAugmentation) -> dict[str, Any]:
    """Extra result fields carried only by the binary-selection-bits diagnostic path."""
    return {
        "binary_selection_bits": bits,
        "comment_bits": post_augmentation.get("commentBits", ""),
        "angle_bits": post_augmentation.get("angleBits", ""),
        "compression_skipped": True,
        "payload_transform_skipped": True,
    }
