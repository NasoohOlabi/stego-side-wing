"""Sample-layer rollups for POLYCARRIER multi-comment embeddings.

Frame-level metrics live in ``build_zlg_method_comparison_dataset``. This module
implements the **sample / payload** layer formulas in
``docs/plans/polycarrier/metrics-multicomment.md`` §§1.2–6.2.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, validate_call

from services.stego_metrics_service import (
    extract_comment_counter,
    js_divergence,
    kl_divergence,
    tokenize,
)

MethodName = Literal["our_method", "zlg"]


class PolycarrierFrameRow(BaseModel):
    """One paired-row grain (ours or ZLG) belonging to a multi-frame sample."""

    model_config = ConfigDict(extra="ignore")

    sample_index: int
    post_id: str = ""
    method: MethodName
    stegotext: str = ""
    word_count: float | None = None
    payload_bits_encoded: float | None = None
    perplexity_gpt2: float | None = None
    kl_matched_post: float | None = None
    jsd_matched_post: float | None = None
    kl_global_corpus: float | None = None
    jsd_global_corpus: float | None = None
    quality_passed: bool | None = None
    bleu: float | None = None
    rougeL: float | None = None
    bertscore_f1: float | None = None
    accepted: bool | None = None
    decode_ok: bool | None = None


class PolycarrierSampleRecord(BaseModel):
    """One ours multi-frame encode record from ``summary.records[]``."""

    model_config = ConfigDict(extra="ignore")

    sample_index: int
    succeeded: bool = False
    frame_count: int | None = None
    post_ids: list[str] = Field(default_factory=list)
    recovery_meta: dict[str, Any] = Field(default_factory=dict)
    decode: dict[str, Any] | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)


class SampleCapacityRollup(BaseModel):
    useful_bits_sample: int | None = None
    control_bits_sample: int | None = None
    sum_frame_bits_ours: int = 0
    sum_words_ours: float = 0.0
    bpw_sample_ours: float | None = None
    bpw_gross_ours: float | None = None
    utilization_percent: float | None = None
    useful_bits_zlg_sample: float = 0.0
    sum_words_zlg: float = 0.0
    bpw_sample_zlg: float | None = None


class SampleQualityRollup(BaseModel):
    perplexity_gpt2_ours: float | None = None
    perplexity_gpt2_zlg: float | None = None
    ppl_pool_weight: str = "word_count"
    mean_frame_kl_matched_ours: float | None = None
    mean_frame_kl_matched_zlg: float | None = None
    kl_matched_concat_ours: float | None = None
    jsd_matched_concat_ours: float | None = None
    kl_global_concat_ours: float | None = None
    jsd_global_concat_ours: float | None = None
    kl_matched_concat_zlg: float | None = None
    jsd_matched_concat_zlg: float | None = None
    kl_global_concat_zlg: float | None = None
    jsd_global_concat_zlg: float | None = None
    word_count_sample_ours: float = 0.0
    word_count_sample_zlg: float = 0.0
    unique_token_ratio_ours: float | None = None
    unique_token_ratio_zlg: float | None = None
    repetition_ratio_ours: float | None = None
    repetition_ratio_zlg: float | None = None
    gate_pass_rate_ours: float | None = None
    gate_all_pass_ours: bool | None = None
    gate_pass_rate_zlg: float | None = None
    gate_all_pass_zlg: bool | None = None
    bleu_sample_ours: float | None = None
    bleu_sample_zlg: float | None = None
    rougeL_sample_ours: float | None = None
    rougeL_sample_zlg: float | None = None
    bertscore_f1_sample_ours: float | None = None
    bertscore_f1_sample_zlg: float | None = None


class SampleAcceptanceRollup(BaseModel):
    sample_encode_ok: bool
    sample_decode_ok: bool
    sample_frame_count: int
    ours_end_to_end_ok: bool
    zlg_frames_accepted: int = 0
    zlg_sample_all_frames_ok: bool | None = None
    zlg_sample_frame_accept_rate: float | None = None


class SampleLayerRow(BaseModel):
    sample_index: int
    post_ids: list[str]
    primary_post_id: str | None = None
    capacity: SampleCapacityRollup
    quality: SampleQualityRollup
    acceptance: SampleAcceptanceRollup


class SampleLayerReport(BaseModel):
    layer: Literal["sample"] = "sample"
    formula_source: str = "docs/plans/polycarrier/metrics-multicomment.md"
    samples: list[SampleLayerRow]
    ours_end_to_end_success_rate: float | None = None
    samples_attempted: int = 0
    samples_end_to_end_ok: int = 0
    paired_statistics: dict[str, Any] = Field(default_factory=dict)
    cluster_rule: str = (
        "sample_index descriptive unit; optional primary_post_id cluster "
        "(first post_id in records[s].post_ids) disclosed in paired_statistics"
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(num: float, den: float) -> float | None:
    if den <= 0:
        return None
    return num / den


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _frame_word_count(row: PolycarrierFrameRow) -> float:
    if row.word_count is not None:
        return float(row.word_count)
    return float(len(tokenize(row.stegotext)))


def _entry_bits(entries: Sequence[Mapping[str, Any]]) -> int:
    total = 0
    for entry in entries:
        bits = entry.get("embedded_bits")
        if bits is None:
            continue
        total += int(bits)
    return total


def _useful_bits(record: PolycarrierSampleRecord) -> int | None:
    meta = record.recovery_meta or {}
    payload_bits = meta.get("payload_bit_length")
    if payload_bits is not None:
        return int(payload_bits)
    return None


def _control_bits(record: PolycarrierSampleRecord) -> int | None:
    meta = record.recovery_meta or {}
    control = meta.get("control_bit_length")
    if control is not None:
        return int(control)
    return None


@validate_call
def pooled_perplexity(ppls: Sequence[float], weights: Sequence[float]) -> float | None:
    """Token-/word-weighted pool: exp(Σ log(PPL)·w / Σ w)."""
    pairs = [(p, w) for p, w in zip(ppls, weights, strict=True) if p > 0 and w > 0]
    if not pairs:
        return None
    log_sum = sum(math.log(p) * w for p, w in pairs)
    weight_sum = sum(w for _, w in pairs)
    return math.exp(log_sum / weight_sum)


def _unique_ratio(texts: Sequence[str]) -> float | None:
    tokens = [tok for text in texts for tok in tokenize(text)]
    if not tokens:
        return None
    return len(set(tokens)) / len(tokens)


def _gate_stats(flags: Sequence[bool | None]) -> tuple[float | None, bool | None]:
    known = [bool(flag) for flag in flags if flag is not None]
    if not known:
        return None, None
    rate = sum(1 for flag in known if flag) / len(known)
    return rate, all(known)


def _macro_mean(rows: Sequence[PolycarrierFrameRow], field: str) -> float | None:
    values = [_as_float(getattr(row, field)) for row in rows]
    present = [value for value in values if value is not None]
    return _mean(present)


def _method_rows(
    rows: Sequence[PolycarrierFrameRow], method: MethodName
) -> list[PolycarrierFrameRow]:
    return [row for row in rows if row.method == method]


def _capacity_ours(
    record: PolycarrierSampleRecord, ours: Sequence[PolycarrierFrameRow]
) -> SampleCapacityRollup:
    useful = _useful_bits(record)
    control = _control_bits(record)
    sum_bits = _entry_bits(record.entries) or sum(
        int(row.payload_bits_encoded or 0) for row in ours
    )
    sum_words = sum(_frame_word_count(row) for row in ours)
    gross = None if useful is None or control is None else useful + control
    return SampleCapacityRollup(
        useful_bits_sample=useful,
        control_bits_sample=control,
        sum_frame_bits_ours=sum_bits,
        sum_words_ours=sum_words,
        bpw_sample_ours=_safe_div(float(useful), sum_words) if useful is not None else None,
        bpw_gross_ours=_safe_div(float(gross), sum_words) if gross is not None else None,
        utilization_percent=(
            _safe_div(float(gross) * 100.0, float(sum_bits)) if gross is not None else None
        ),
    )


def _capacity_with_zlg(
    base: SampleCapacityRollup, zlg: Sequence[PolycarrierFrameRow]
) -> SampleCapacityRollup:
    useful_zlg = sum(float(row.payload_bits_encoded or 0) for row in zlg)
    words_zlg = sum(_frame_word_count(row) for row in zlg)
    return base.model_copy(
        update={
            "useful_bits_zlg_sample": useful_zlg,
            "sum_words_zlg": words_zlg,
            "bpw_sample_zlg": _safe_div(useful_zlg, words_zlg),
        }
    )


def _ppl_pairs(rows: Sequence[PolycarrierFrameRow]) -> tuple[list[float], list[float]]:
    ppls: list[float] = []
    weights: list[float] = []
    for row in rows:
        if row.perplexity_gpt2 is None:
            continue
        ppl = float(row.perplexity_gpt2)
        if ppl <= 0:
            continue
        ppls.append(ppl)
        weights.append(_frame_word_count(row))
    return ppls, weights


def _quality_for_method(
    rows: Sequence[PolycarrierFrameRow],
) -> dict[str, float | bool | None]:
    texts = [row.stegotext for row in rows]
    ppls, weights = _ppl_pairs(rows)
    uniq = _unique_ratio(texts)
    gate_rate, gate_all = _gate_stats([row.quality_passed for row in rows])
    kl_vals = [float(row.kl_matched_post) for row in rows if row.kl_matched_post is not None]
    return {
        "ppl": pooled_perplexity(ppls, weights),
        "mean_kl": _mean(kl_vals),
        "words": sum(_frame_word_count(row) for row in rows),
        "unique": uniq,
        "repetition": None if uniq is None else 1.0 - uniq,
        "gate_rate": gate_rate,
        "gate_all": gate_all,
        "bleu": _macro_mean(rows, "bleu"),
        "rougeL": _macro_mean(rows, "rougeL"),
        "bertscore_f1": _macro_mean(rows, "bertscore_f1"),
    }


def _concat_text(rows: Sequence[PolycarrierFrameRow]) -> str:
    return " ".join(row.stegotext.strip() for row in rows if row.stegotext.strip())


def _resolve_post_path(dataset_dir: Path, post_id: str) -> Path | None:
    direct = dataset_dir / f"{post_id}.json"
    if direct.is_file():
        return direct
    matches = sorted(dataset_dir.glob(f"{post_id}*.json"))
    return matches[0] if matches else None


def _matched_baseline(dataset_dir: Path, post_ids: Sequence[str]) -> Counter:
    baseline: Counter = Counter()
    for post_id in post_ids:
        path = _resolve_post_path(dataset_dir, post_id)
        if path is None:
            continue
        baseline.update(extract_comment_counter(path))
    return baseline


def _global_baseline(dataset_dir: Path) -> Counter:
    baseline: Counter = Counter()
    for path in sorted(dataset_dir.glob("*.json")):
        baseline.update(extract_comment_counter(path))
    return baseline


def _concat_divergence(
    text: str,
    *,
    matched: Counter,
    global_counter: Counter,
    alpha: float,
) -> dict[str, float | None]:
    stego = Counter(tokenize(text))
    if not stego:
        return {
            "kl_matched": None,
            "jsd_matched": None,
            "kl_global": None,
            "jsd_global": None,
        }
    return {
        "kl_matched": kl_divergence(stego, matched, alpha) if matched else None,
        "jsd_matched": js_divergence(stego, matched, alpha) if matched else None,
        "kl_global": kl_divergence(stego, global_counter, alpha) if global_counter else None,
        "jsd_global": js_divergence(stego, global_counter, alpha) if global_counter else None,
    }


def _acceptance(
    record: PolycarrierSampleRecord, zlg: Sequence[PolycarrierFrameRow]
) -> SampleAcceptanceRollup:
    decode = record.decode or {}
    encode_ok = bool(record.succeeded)
    decode_ok = bool(decode.get("payload_match"))
    frame_count = int(record.frame_count or len(record.entries) or 0)
    accepted = 0
    for row in zlg:
        if row.accepted is True or (row.accepted is None and row.decode_ok is True):
            accepted += 1
    rate = _safe_div(float(accepted), float(frame_count)) if frame_count else None
    return SampleAcceptanceRollup(
        sample_encode_ok=encode_ok,
        sample_decode_ok=decode_ok,
        sample_frame_count=frame_count,
        ours_end_to_end_ok=encode_ok and decode_ok,
        zlg_frames_accepted=accepted,
        zlg_sample_all_frames_ok=(accepted == frame_count) if frame_count else None,
        zlg_sample_frame_accept_rate=rate,
    )


def _float_or_none(value: float | bool | None) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _quality_half(
    prefix: str, stats: dict[str, float | bool | None]
) -> dict[str, float | bool | None]:
    return {
        f"perplexity_gpt2_{prefix}": stats["ppl"],
        f"mean_frame_kl_matched_{prefix}": stats["mean_kl"],
        f"word_count_sample_{prefix}": float(_float_or_none(stats["words"]) or 0.0),
        f"unique_token_ratio_{prefix}": stats["unique"],
        f"repetition_ratio_{prefix}": stats["repetition"],
        f"gate_pass_rate_{prefix}": stats["gate_rate"],
        f"gate_all_pass_{prefix}": stats["gate_all"],
        f"bleu_sample_{prefix}": stats["bleu"],
        f"rougeL_sample_{prefix}": stats["rougeL"],
        f"bertscore_f1_sample_{prefix}": stats["bertscore_f1"],
    }


def _build_quality(
    ours: Sequence[PolycarrierFrameRow], zlg: Sequence[PolycarrierFrameRow]
) -> SampleQualityRollup:
    payload = {
        **_quality_half("ours", _quality_for_method(ours)),
        **_quality_half("zlg", _quality_for_method(zlg)),
    }
    return SampleQualityRollup.model_validate(payload)


def _maybe_concat_quality(
    quality: SampleQualityRollup,
    ours: Sequence[PolycarrierFrameRow],
    zlg: Sequence[PolycarrierFrameRow],
    record: PolycarrierSampleRecord,
    *,
    dataset_dir: Path | None,
    alpha: float,
    matched_baseline: Counter | None,
    global_baseline: Counter | None,
) -> SampleQualityRollup:
    if dataset_dir is None and matched_baseline is None:
        return quality
    return _attach_concat_divergence(
        quality,
        ours,
        zlg,
        record.post_ids,
        dataset_dir=dataset_dir,
        alpha=alpha,
        matched_baseline=matched_baseline,
        global_baseline=global_baseline,
    )


def _sample_row(
    record: PolycarrierSampleRecord,
    *,
    ours: Sequence[PolycarrierFrameRow],
    zlg: Sequence[PolycarrierFrameRow],
    quality: SampleQualityRollup,
) -> SampleLayerRow:
    primary = record.post_ids[0] if record.post_ids else None
    return SampleLayerRow(
        sample_index=record.sample_index,
        post_ids=list(record.post_ids),
        primary_post_id=primary,
        capacity=_capacity_with_zlg(_capacity_ours(record, ours), zlg),
        quality=quality,
        acceptance=_acceptance(record, zlg),
    )


@validate_call
def rollup_one_sample(
    record: PolycarrierSampleRecord,
    frame_rows: Sequence[PolycarrierFrameRow] = (),
    *,
    dataset_dir: Path | None = None,
    alpha: float = 1e-6,
    matched_baseline: Counter | None = None,
    global_baseline: Counter | None = None,
) -> SampleLayerRow:
    """Roll one sample's ours record + optional paired frame rows into sample metrics."""
    ours = _method_rows(frame_rows, "our_method")
    zlg = _method_rows(frame_rows, "zlg")
    quality = _maybe_concat_quality(
        _build_quality(ours, zlg),
        ours,
        zlg,
        record,
        dataset_dir=dataset_dir,
        alpha=alpha,
        matched_baseline=matched_baseline,
        global_baseline=global_baseline,
    )
    return _sample_row(record, ours=ours, zlg=zlg, quality=quality)


def _resolve_baselines(
    *,
    dataset_dir: Path | None,
    post_ids: Sequence[str],
    matched_baseline: Counter | None,
    global_baseline: Counter | None,
) -> tuple[Counter | None, Counter | None]:
    matched = matched_baseline
    global_counter = global_baseline
    if dataset_dir is None:
        return matched, global_counter
    if matched is None:
        matched = _matched_baseline(dataset_dir, post_ids)
    if global_counter is None:
        global_counter = _global_baseline(dataset_dir)
    return matched, global_counter


def _divergence_update(
    ours_div: dict[str, float | None], zlg_div: dict[str, float | None]
) -> dict[str, float | None]:
    return {
        "kl_matched_concat_ours": ours_div["kl_matched"],
        "jsd_matched_concat_ours": ours_div["jsd_matched"],
        "kl_global_concat_ours": ours_div["kl_global"],
        "jsd_global_concat_ours": ours_div["jsd_global"],
        "kl_matched_concat_zlg": zlg_div["kl_matched"],
        "jsd_matched_concat_zlg": zlg_div["jsd_matched"],
        "kl_global_concat_zlg": zlg_div["kl_global"],
        "jsd_global_concat_zlg": zlg_div["jsd_global"],
    }


def _pair_divergences(
    ours: Sequence[PolycarrierFrameRow],
    zlg: Sequence[PolycarrierFrameRow],
    *,
    matched: Counter,
    global_counter: Counter,
    alpha: float,
) -> dict[str, float | None]:
    ours_div = _concat_divergence(
        _concat_text(ours), matched=matched, global_counter=global_counter, alpha=alpha
    )
    zlg_div = _concat_divergence(
        _concat_text(zlg), matched=matched, global_counter=global_counter, alpha=alpha
    )
    return _divergence_update(ours_div, zlg_div)


def _attach_concat_divergence(
    quality: SampleQualityRollup,
    ours: Sequence[PolycarrierFrameRow],
    zlg: Sequence[PolycarrierFrameRow],
    post_ids: Sequence[str],
    *,
    dataset_dir: Path | None,
    alpha: float,
    matched_baseline: Counter | None,
    global_baseline: Counter | None,
) -> SampleQualityRollup:
    matched, global_counter = _resolve_baselines(
        dataset_dir=dataset_dir,
        post_ids=post_ids,
        matched_baseline=matched_baseline,
        global_baseline=global_baseline,
    )
    if matched is None or global_counter is None:
        return quality
    return quality.model_copy(
        update=_pair_divergences(
            ours, zlg, matched=matched, global_counter=global_counter, alpha=alpha
        )
    )

def _frames_by_sample(
    rows: Iterable[PolycarrierFrameRow],
) -> dict[int, list[PolycarrierFrameRow]]:
    grouped: dict[int, list[PolycarrierFrameRow]] = {}
    for row in rows:
        grouped.setdefault(row.sample_index, []).append(row)
    return grouped


def _rollup_records(
    records: Sequence[PolycarrierSampleRecord],
    by_sample: Mapping[int, list[PolycarrierFrameRow]],
    *,
    dataset_dir: Path | None,
    alpha: float,
    global_cache: Counter | None,
) -> list[SampleLayerRow]:
    samples: list[SampleLayerRow] = []
    for record in records:
        matched = (
            _matched_baseline(dataset_dir, record.post_ids) if dataset_dir is not None else None
        )
        samples.append(
            rollup_one_sample(
                record,
                by_sample.get(record.sample_index, []),
                dataset_dir=dataset_dir,
                alpha=alpha,
                matched_baseline=matched,
                global_baseline=global_cache,
            )
        )
    return samples


@validate_call
def build_sample_layer_report(
    records: Sequence[PolycarrierSampleRecord],
    frame_rows: Sequence[PolycarrierFrameRow] = (),
    *,
    dataset_dir: Path | None = None,
    alpha: float = 1e-6,
    cluster_by_primary_post: bool = False,
) -> SampleLayerReport:
    """Build the full sample-layer report for a multi-frame run."""
    by_sample = _frames_by_sample(frame_rows)
    global_cache = _global_baseline(dataset_dir) if dataset_dir is not None else None
    samples = _rollup_records(
        records, by_sample, dataset_dir=dataset_dir, alpha=alpha, global_cache=global_cache
    )
    ok = sum(1 for row in samples if row.acceptance.ours_end_to_end_ok)
    attempted = len(samples)
    return SampleLayerReport(
        samples=samples,
        samples_attempted=attempted,
        samples_end_to_end_ok=ok,
        ours_end_to_end_success_rate=_safe_div(float(ok), float(attempted)),
        paired_statistics=sample_paired_statistics(
            samples, cluster_by_primary_post=cluster_by_primary_post
        ),
    )


def parse_summary_records(summary: Mapping[str, Any]) -> list[PolycarrierSampleRecord]:
    raw = summary.get("records") or []
    return [PolycarrierSampleRecord.model_validate(item) for item in raw]


def parse_paired_rows(rows: Sequence[Mapping[str, Any]]) -> list[PolycarrierFrameRow]:
    parsed: list[PolycarrierFrameRow] = []
    for row in rows:
        method = row.get("method")
        if method not in ("our_method", "zlg"):
            continue
        if row.get("sample_index") is None:
            continue
        parsed.append(PolycarrierFrameRow.model_validate(row))
    return parsed


# (metric_name, ours accessor path, zlg accessor path) — sample-layer §7 companion.
_SAMPLE_PAIR_METRICS: tuple[tuple[str, str, str], ...] = (
    ("bpw_sample", "capacity.bpw_sample_ours", "capacity.bpw_sample_zlg"),
    ("perplexity_gpt2", "quality.perplexity_gpt2_ours", "quality.perplexity_gpt2_zlg"),
    ("kl_matched_concat", "quality.kl_matched_concat_ours", "quality.kl_matched_concat_zlg"),
    ("jsd_matched_concat", "quality.jsd_matched_concat_ours", "quality.jsd_matched_concat_zlg"),
    ("word_count_sample", "quality.word_count_sample_ours", "quality.word_count_sample_zlg"),
    ("bleu_sample", "quality.bleu_sample_ours", "quality.bleu_sample_zlg"),
    ("rougeL_sample", "quality.rougeL_sample_ours", "quality.rougeL_sample_zlg"),
    ("bertscore_f1_sample", "quality.bertscore_f1_sample_ours", "quality.bertscore_f1_sample_zlg"),
    ("gate_pass_rate", "quality.gate_pass_rate_ours", "quality.gate_pass_rate_zlg"),
)


def _nested_float(row: SampleLayerRow, dotted: str) -> float | None:
    cur: Any = row
    for part in dotted.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return _as_float(cur)


def _sign_test_p(positive: int, negative: int) -> float | None:
    total = positive + negative
    if total == 0:
        return None
    observed = min(positive, negative)
    tail = sum(math.comb(total, k) for k in range(observed + 1)) / (2**total)
    return min(1.0, 2.0 * tail)


def _holm_adjust(stats: dict[str, Any]) -> None:
    tests = sorted(
        (
            (key, float(block["two_sided_sign_test_p"]))
            for key, block in stats.items()
            if isinstance(block, dict)
            and isinstance(block.get("two_sided_sign_test_p"), (int, float))
        ),
        key=lambda item: item[1],
    )
    running_max = 0.0
    total = len(tests)
    for rank, (key, p_value) in enumerate(tests):
        running_max = max(running_max, min(1.0, (total - rank) * p_value))
        stats[key]["holm_adjusted_p"] = running_max
        stats[key]["multiple_testing_family_size"] = total


def _metric_deltas(
    samples: Sequence[SampleLayerRow], ours_path: str, zlg_path: str
) -> list[float]:
    deltas: list[float] = []
    for row in samples:
        ours = _nested_float(row, ours_path)
        zlg = _nested_float(row, zlg_path)
        if ours is None or zlg is None:
            continue
        if not math.isfinite(ours) or not math.isfinite(zlg):
            continue
        deltas.append(zlg - ours)
    return deltas


def _delta_block(deltas: Sequence[float]) -> dict[str, Any]:
    positive = sum(1 for delta in deltas if delta > 0)
    negative = sum(1 for delta in deltas if delta < 0)
    return {
        "n": len(deltas),
        "mean_delta_zlg_minus_our": sum(deltas) / len(deltas),
        "zlg_greater_count": positive,
        "our_greater_count": negative,
        "ties": len(deltas) - positive - negative,
        "two_sided_sign_test_p": _sign_test_p(positive, negative),
    }


def _cluster_means(
    samples: Sequence[SampleLayerRow], ours_path: str, zlg_path: str
) -> list[tuple[float, float]]:
    buckets: dict[str, list[tuple[float, float]]] = {}
    for row in samples:
        ours = _nested_float(row, ours_path)
        zlg = _nested_float(row, zlg_path)
        if ours is None or zlg is None:
            continue
        key = row.primary_post_id or f"sample:{row.sample_index}"
        buckets.setdefault(key, []).append((ours, zlg))
    pairs: list[tuple[float, float]] = []
    for values in buckets.values():
        ours_mean = sum(item[0] for item in values) / len(values)
        zlg_mean = sum(item[1] for item in values) / len(values)
        pairs.append((ours_mean, zlg_mean))
    return pairs


def _fill_metric_blocks(
    out: dict[str, Any],
    samples: Sequence[SampleLayerRow],
    *,
    cluster_by_primary_post: bool,
) -> None:
    for name, ours_path, zlg_path in _SAMPLE_PAIR_METRICS:
        deltas = (
            [z - o for o, z in _cluster_means(samples, ours_path, zlg_path)]
            if cluster_by_primary_post
            else _metric_deltas(samples, ours_path, zlg_path)
        )
        if deltas:
            out[name] = _delta_block(deltas)


@validate_call
def sample_paired_statistics(
    samples: Sequence[SampleLayerRow],
    *,
    cluster_by_primary_post: bool = False,
) -> dict[str, Any]:
    """Sign test + Holm on sample-layer ours↔ZLG metrics (metrics-multicomment §7)."""
    rule = (
        "primary_post_id = first post_ids entry"
        if cluster_by_primary_post
        else "sample_index (no post clustering)"
    )
    out: dict[str, Any] = {
        "paired_n": len(samples),
        "cluster_by_primary_post": cluster_by_primary_post,
        "cluster_rule": rule,
    }
    _fill_metric_blocks(out, samples, cluster_by_primary_post=cluster_by_primary_post)
    _holm_adjust(out)
    return out
