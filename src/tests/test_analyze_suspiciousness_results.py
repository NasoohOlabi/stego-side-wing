from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "analyze_suspiciousness_results.py"
    spec = importlib.util.spec_from_file_location("analyze_suspiciousness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_analysis_clusters_repeated_judgments_by_post() -> None:
    module = _load_module()
    rows = [
        {"post_id": "p1", "method": "our_method", "valid": True, "correct": False},
        {"post_id": "p1", "method": "our_method", "valid": True, "correct": False},
        {"post_id": "p1", "method": "zlg", "valid": True, "correct": True},
        {"post_id": "p1", "method": "zlg", "valid": True, "correct": False},
        {"post_id": "p2", "method": "our_method", "valid": True, "correct": False},
        {"post_id": "p2", "method": "zlg", "valid": True, "correct": True},
    ]

    report = module.analyze(rows, iterations=1000)

    assert report["independent_post_clusters"] == 2
    assert report["valid_rows"] == 6
    assert report["mean_detection_rate_delta_zlg_minus_our"] == 0.75
