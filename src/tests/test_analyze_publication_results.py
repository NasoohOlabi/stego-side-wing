from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "analyze_publication_results.py"
    spec = importlib.util.spec_from_file_location("analyze_publication_results", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_keeps_failures_in_attempt_denominator() -> None:
    module = _load_module()
    rows = [
        {
            "post_id": "p1",
            "method": "our_method",
            "accepted": True,
            "decode_ok": True,
            "payload_bits_encoded": 64,
            "word_count": 32,
            "latency_ms": 10,
        },
        {
            "post_id": "p2",
            "method": "our_method",
            "accepted": False,
            "decode_ok": False,
            "reason": "generation",
            "latency_ms": 5,
        },
        {
            "post_id": "p1",
            "method": "official_zgls",
            "accepted": True,
            "decode_ok": True,
            "payload_bits_encoded": 64,
            "word_count": 16,
            "latency_ms": 20,
        },
        {
            "post_id": "p2",
            "method": "official_zgls",
            "accepted": True,
            "decode_ok": True,
            "payload_bits_encoded": 64,
            "word_count": 16,
            "latency_ms": 20,
        },
    ]

    report = module.build_report(rows, [])

    assert report["methods"]["our_method"]["attempt_success_rate"] == 0.5
    assert report["methods"]["official_zgls"]["attempt_success_rate"] == 1.0
    assert report["methods"]["our_method"]["failure_taxonomy"] == {"generation": 1}
    assert report["paired_comparisons"]["effective_recovered_bits_per_word"]["paired_posts"] == 1
