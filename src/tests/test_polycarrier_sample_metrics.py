"""Unit tests for POLYCARRIER sample-layer metric rollups."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from services.polycarrier_sample_metrics import (
    PolycarrierFrameRow,
    PolycarrierSampleRecord,
    build_sample_layer_report,
    parse_paired_rows,
    parse_summary_records,
    pooled_perplexity,
    rollup_one_sample,
)


def test_pooled_perplexity_token_weighted() -> None:
    assert pooled_perplexity([math.e, math.e], [1, 1]) == pytest.approx(math.e)
    pooled = pooled_perplexity([math.e, math.e**3], [1.0, 3.0])
    assert pooled == pytest.approx(math.exp(2.5))


def test_pooled_perplexity_rejects_empty() -> None:
    assert pooled_perplexity([], []) is None
    assert pooled_perplexity([0.0], [1.0]) is None


def test_capacity_and_acceptance_from_record_only() -> None:
    record = PolycarrierSampleRecord(
        sample_index=0,
        succeeded=True,
        frame_count=3,
        post_ids=["p1", "p2"],
        recovery_meta={"control_bit_length": 22, "payload_bit_length": 256},
        decode={"payload_match": True},
        entries=[
            {"embedded_bits": 15},
            {"embedded_bits": 18},
            {"embedded_bits": 20},
        ],
    )
    row = rollup_one_sample(record, [])
    assert row.capacity.useful_bits_sample == 256
    assert row.capacity.control_bits_sample == 22
    assert row.capacity.sum_frame_bits_ours == 53
    assert row.capacity.utilization_percent is not None
    assert abs(row.capacity.utilization_percent - (278 / 53) * 100) < 1e-9
    assert row.acceptance.ours_end_to_end_ok is True
    assert row.acceptance.sample_frame_count == 3


def test_quality_rollups_from_paired_frames() -> None:
    record = PolycarrierSampleRecord(
        sample_index=1,
        succeeded=True,
        frame_count=2,
        post_ids=["p1"],
        recovery_meta={"control_bit_length": 10, "payload_bit_length": 32},
        decode={"payload_match": True},
        entries=[{"embedded_bits": 20}, {"embedded_bits": 20}],
    )
    frames = [
        PolycarrierFrameRow(
            sample_index=1,
            post_id="p1",
            method="our_method",
            stegotext="alpha beta gamma",
            word_count=3,
            payload_bits_encoded=20,
            perplexity_gpt2=math.e,
            kl_matched_post=0.5,
            quality_passed=True,
            bleu=10.0,
            rougeL=0.4,
            bertscore_f1=0.8,
        ),
        PolycarrierFrameRow(
            sample_index=1,
            post_id="p1",
            method="our_method",
            stegotext="alpha delta",
            word_count=2,
            payload_bits_encoded=20,
            perplexity_gpt2=math.e,
            kl_matched_post=1.5,
            quality_passed=False,
            bleu=20.0,
            rougeL=0.6,
            bertscore_f1=0.6,
        ),
        PolycarrierFrameRow(
            sample_index=1,
            post_id="p1",
            method="zlg",
            stegotext="zeta eta theta",
            word_count=3,
            payload_bits_encoded=16,
            perplexity_gpt2=math.e**2,
            quality_passed=True,
            accepted=True,
            decode_ok=True,
        ),
        PolycarrierFrameRow(
            sample_index=1,
            post_id="p1",
            method="zlg",
            stegotext="iota",
            word_count=1,
            payload_bits_encoded=8,
            perplexity_gpt2=math.e**2,
            quality_passed=True,
            accepted=False,
            decode_ok=False,
        ),
    ]
    row = rollup_one_sample(record, frames)
    assert row.capacity.bpw_sample_ours is not None
    assert abs(row.capacity.bpw_sample_ours - 32 / 5) < 1e-9
    assert row.capacity.useful_bits_zlg_sample == 24
    assert row.quality.perplexity_gpt2_ours is not None
    assert abs(row.quality.perplexity_gpt2_ours - math.e) < 1e-9
    assert row.quality.mean_frame_kl_matched_ours == 1.0
    assert row.quality.bleu_sample_ours == 15.0
    assert row.quality.gate_pass_rate_ours == 0.5
    assert row.quality.gate_all_pass_ours is False
    assert row.quality.unique_token_ratio_ours is not None
    # tokens: alpha beta gamma alpha delta → 5 tokens, 4 unique
    assert abs(row.quality.unique_token_ratio_ours - 0.8) < 1e-9
    assert row.acceptance.zlg_frames_accepted == 1
    assert row.acceptance.zlg_sample_all_frames_ok is False
    assert row.acceptance.zlg_sample_frame_accept_rate == 0.5


def test_concat_kl_with_dataset_dir(tmp_path: Path) -> None:
    post = {
        "id": "p1",
        "comments": [
            {"body": "alpha beta gamma delta", "replies": []},
            {"body": "epsilon zeta eta theta", "replies": []},
        ],
    }
    (tmp_path / "p1.json").write_text(json.dumps(post), encoding="utf-8")
    record = PolycarrierSampleRecord(
        sample_index=0,
        succeeded=True,
        frame_count=1,
        post_ids=["p1"],
        recovery_meta={"control_bit_length": 4, "payload_bit_length": 8},
        decode={"payload_match": True},
        entries=[{"embedded_bits": 16}],
    )
    frames = [
        PolycarrierFrameRow(
            sample_index=0,
            post_id="p1",
            method="our_method",
            stegotext="alpha beta gamma delta epsilon zeta eta theta",
            word_count=8,
            payload_bits_encoded=16,
            quality_passed=True,
        )
    ]
    row = rollup_one_sample(record, frames, dataset_dir=tmp_path)
    assert row.quality.kl_matched_concat_ours is not None
    assert row.quality.kl_matched_concat_ours < 1e-6
    assert row.quality.jsd_matched_concat_ours is not None
    assert row.quality.kl_global_concat_ours is not None


def test_build_report_success_rate() -> None:
    records = [
        PolycarrierSampleRecord(
            sample_index=0,
            succeeded=True,
            frame_count=2,
            post_ids=["a"],
            recovery_meta={"control_bit_length": 1, "payload_bit_length": 8},
            decode={"payload_match": True},
            entries=[{"embedded_bits": 10}, {"embedded_bits": 10}],
        ),
        PolycarrierSampleRecord(
            sample_index=1,
            succeeded=True,
            frame_count=2,
            post_ids=["b"],
            recovery_meta={"control_bit_length": 1, "payload_bit_length": 8},
            decode={"payload_match": False},
            entries=[{"embedded_bits": 10}, {"embedded_bits": 10}],
        ),
    ]
    report = build_sample_layer_report(records, [])
    assert report.samples_attempted == 2
    assert report.samples_end_to_end_ok == 1
    assert report.ours_end_to_end_success_rate == 0.5


def test_parse_summary_and_paired_rows() -> None:
    summary = {
        "records": [
            {
                "sample_index": 0,
                "succeeded": True,
                "frame_count": 1,
                "post_ids": ["x"],
                "recovery_meta": {"control_bit_length": 2, "payload_bit_length": 8},
                "decode": {"payload_match": True},
                "entries": [{"embedded_bits": 12}],
            }
        ]
    }
    records = parse_summary_records(summary)
    assert len(records) == 1
    rows = parse_paired_rows(
        [
            {"sample_index": 0, "method": "our_method", "stegotext": "hi", "word_count": 1},
            {"sample_index": 0, "method": "ignored"},
            {"method": "zlg", "stegotext": "no sample"},
        ]
    )
    assert len(rows) == 1
    assert rows[0].method == "our_method"


def test_sample_paired_statistics_sign_and_holm() -> None:
    from services.polycarrier_sample_metrics import sample_paired_statistics

    samples = []
    for index in range(4):
        record = PolycarrierSampleRecord(
            sample_index=index,
            succeeded=True,
            frame_count=1,
            post_ids=[f"p{index}"],
            recovery_meta={"control_bit_length": 1, "payload_bit_length": 8},
            decode={"payload_match": True},
            entries=[{"embedded_bits": 16}],
        )
        frames = [
            PolycarrierFrameRow(
                sample_index=index,
                post_id=f"p{index}",
                method="our_method",
                stegotext="alpha beta",
                word_count=2,
                payload_bits_encoded=16,
                perplexity_gpt2=10.0 + index,
                quality_passed=True,
            ),
            PolycarrierFrameRow(
                sample_index=index,
                post_id=f"p{index}",
                method="zlg",
                stegotext="alpha beta gamma",
                word_count=3,
                payload_bits_encoded=16,
                perplexity_gpt2=20.0 + index,
                quality_passed=True,
                accepted=True,
            ),
        ]
        samples.append(rollup_one_sample(record, frames))
    stats = sample_paired_statistics(samples)
    assert stats["paired_n"] == 4
    assert "perplexity_gpt2" in stats
    assert stats["perplexity_gpt2"]["n"] == 4
    assert stats["perplexity_gpt2"]["mean_delta_zlg_minus_our"] == pytest.approx(10.0)
    assert "holm_adjusted_p" in stats["perplexity_gpt2"]
    assert "bpw_sample" in stats
