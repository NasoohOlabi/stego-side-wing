"""Shared helpers for per-sample and aggregate stego experiment metrics."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from pydantic import validate_call

from workflows.utils.stego_codec import extract_invisible_payload


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _safe_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _safe_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _counter_rows(counter: Counter[str], *, limit: int = 5) -> list[dict[str, Any]]:
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def _shannon_entropy(counter: Counter[str]) -> float:
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counter.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


@validate_call
def build_sample_experiment_metrics(
    stego_result: dict[str, Any],
    *,
    stego_text: str,
    payload_bytes: int,
    receiver_decode: dict[str, Any] | None = None,
) -> dict[str, Any]:
    embedding = stego_result.get("embedding")
    embedding_dict = embedding if isinstance(embedding, dict) else {}
    compression = embedding_dict.get("compression")
    compression_dict = compression if isinstance(compression, dict) else {}
    comment_embedding = embedding_dict.get("commentEmbedding")
    comment_dict = comment_embedding if isinstance(comment_embedding, dict) else {}
    angle_embedding = embedding_dict.get("angleEmbedding")
    angle_dict = angle_embedding if isinstance(angle_embedding, dict) else {}

    comment_bits = _safe_str(embedding_dict.get("commentBits")) or _safe_str(
        comment_dict.get("bitsUsed")
    )
    angle_bits = _safe_str(embedding_dict.get("angleBits")) or _safe_str(angle_dict.get("bitsUsed"))
    selection_signature = _safe_str(embedding_dict.get("selectionSignature")) or (
        f"{comment_bits}{angle_bits}"
    )
    comment_bits_count = _safe_int(comment_dict.get("bitsCount")) or len(comment_bits)
    angle_bits_count = _safe_int(angle_dict.get("bitsCount")) or len(angle_bits)
    selection_bits = comment_bits_count + angle_bits_count

    hidden_payload = extract_invisible_payload(stego_text) or ""
    hidden_payload_bytes = len(hidden_payload.encode("utf-8"))
    hidden_payload_bits = hidden_payload_bytes * 8
    stego_bytes = max(1, len(stego_text.encode("utf-8")))
    total_bits = hidden_payload_bits + selection_bits

    compression_ratio = _safe_float(compression_dict.get("ratio"))
    hidden_expansion_ratio = hidden_payload_bytes / max(1, payload_bytes)

    return {
        "receiver_success": receiver_decode is not None,
        "carrier_metrics": {
            "hidden_payload_bytes": hidden_payload_bytes,
            "hidden_expansion_ratio": hidden_expansion_ratio,
            "compression_method": compression_dict.get("method"),
            "compression_ratio": compression_ratio,
        },
        "selection_metrics": {
            "comment_bits": comment_bits,
            "angle_bits": angle_bits,
            "selection_signature": selection_signature,
            "comment_bits_count": comment_bits_count,
            "angle_bits_count": angle_bits_count,
            "selection_bits": selection_bits,
        },
        "capacity_metrics": {
            "bps_hidden": hidden_payload_bits / stego_bytes,
            "bps_selection": selection_bits / stego_bytes,
            "bps_total": total_bits / stego_bytes,
            "hidden_payload_bits": hidden_payload_bits,
            "selection_bits": selection_bits,
            "total_bits": total_bits,
            "stego_bytes": stego_bytes,
        },
    }


@validate_call
def build_experiment_summary_metrics(
    entries: list[dict[str, Any]],
    *,
    divergence_report: dict[str, Any] | None,
    perplexity_report: dict[str, Any] | None,
) -> dict[str, Any]:
    carrier_values = [
        entry.get("sample_metrics", {}).get("carrier_metrics", {}) for entry in entries
    ]
    selection_values = [
        entry.get("sample_metrics", {}).get("selection_metrics", {}) for entry in entries
    ]
    capacity_values = [
        entry.get("sample_metrics", {}).get("capacity_metrics", {}) for entry in entries
    ]

    method_counter = Counter(
        str(metrics.get("compression_method"))
        for metrics in carrier_values
        if isinstance(metrics.get("compression_method"), str)
    )
    comment_counter = Counter(
        str(metrics.get("comment_bits"))
        for metrics in selection_values
        if isinstance(metrics.get("comment_bits"), str)
    )
    angle_counter = Counter(
        str(metrics.get("angle_bits"))
        for metrics in selection_values
        if isinstance(metrics.get("angle_bits"), str)
    )
    signature_counter = Counter(
        str(metrics.get("selection_signature"))
        for metrics in selection_values
        if isinstance(metrics.get("selection_signature"), str)
    )
    sample_count = len(entries)
    receiver_success_count = sum(
        1 for entry in entries if bool(entry.get("sample_metrics", {}).get("receiver_success"))
    )

    primary = (
        divergence_report.get("primary_baseline_matched_post", {})
        if isinstance(divergence_report, dict)
        else {}
    )
    secondary = (
        divergence_report.get("secondary_baseline_global_corpus", {})
        if isinstance(divergence_report, dict)
        else {}
    )
    perplexity = (
        perplexity_report.get("perplexity_summary", {})
        if isinstance(perplexity_report, dict)
        else {}
    )

    hidden_payload_bytes = [
        float(metrics["hidden_payload_bytes"])
        for metrics in carrier_values
        if isinstance(metrics.get("hidden_payload_bytes"), (int, float))
    ]
    hidden_expansion_ratios = [
        float(metrics["hidden_expansion_ratio"])
        for metrics in carrier_values
        if isinstance(metrics.get("hidden_expansion_ratio"), (int, float))
    ]
    compression_ratios = [
        float(metrics["compression_ratio"])
        for metrics in carrier_values
        if isinstance(metrics.get("compression_ratio"), (int, float))
    ]
    bps_hidden = [
        float(metrics["bps_hidden"])
        for metrics in capacity_values
        if isinstance(metrics.get("bps_hidden"), (int, float))
    ]
    bps_selection = [
        float(metrics["bps_selection"])
        for metrics in capacity_values
        if isinstance(metrics.get("bps_selection"), (int, float))
    ]
    bps_total = [
        float(metrics["bps_total"])
        for metrics in capacity_values
        if isinstance(metrics.get("bps_total"), (int, float))
    ]

    return {
        "quality_metrics": {
            "matched_post_kl": primary.get("average_kl_stego_vs_matched_post"),
            "matched_post_jsd": primary.get("average_jsd_stego_vs_matched_post"),
            "global_corpus_kl": secondary.get("average_kl_stego_vs_global_corpus"),
            "global_corpus_jsd": secondary.get("average_jsd_stego_vs_global_corpus"),
            "perplexity": perplexity.get("average_perplexity"),
            "receiver_success_rate": (
                receiver_success_count / sample_count if sample_count > 0 else None
            ),
        },
        "carrier_metrics": {
            "hidden_payload_bytes": _mean(hidden_payload_bytes),
            "hidden_expansion_ratio": _mean(hidden_expansion_ratios),
            "compression_method": method_counter.most_common(1)[0][0] if method_counter else None,
            "compression_ratio": _mean(compression_ratios),
            "compression_method_counts": dict(method_counter),
            "standard_fallback_rate": (
                method_counter.get("standard", 0) / sample_count if sample_count > 0 else None
            ),
        },
        "selection_metrics": {
            "comment_bits": _counter_rows(comment_counter),
            "angle_bits": _counter_rows(angle_counter),
            "selection_signature": _counter_rows(signature_counter),
            "selection_signature_entropy": _shannon_entropy(signature_counter),
            "unique_selection_signatures": len(signature_counter),
            "unique_comment_bits": len(comment_counter),
            "unique_angle_bits": len(angle_counter),
        },
        "capacity_metrics": {
            "bps_hidden": _mean(bps_hidden),
            "bps_selection": _mean(bps_selection),
            "bps_total": _mean(bps_total),
        },
    }
