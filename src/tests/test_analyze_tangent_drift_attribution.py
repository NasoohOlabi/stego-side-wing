import importlib.util
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[2] / "scripts" / "analyze_tangent_drift_attribution.py"
    spec = importlib.util.spec_from_file_location("analyze_tangent_drift_attribution", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze = _load().analyze


def _report(search: int, relevance: float) -> dict:
    return {
        "kept_count": 4,
        "source_mix_kept": {"post": 1, "comments": 1, "search_results": search},
        "relevance": {"scores_kept": [relevance]},
        "distinctness": {},
        "dropped": {},
        "config": {},
    }


def test_analyze_clusters_by_post_and_crosses_detection_with_source_quality() -> None:
    paired = [
        {"post_id": "a", "method": "our_method", "tangent_db_report": _report(6, 0.2)},
        {"post_id": "a", "method": "our_method", "tangent_db_report": _report(6, 0.4)},
        {"post_id": "b", "method": "our_method", "tangent_db_report": _report(0, 0.9)},
    ]
    judgments = [
        {
            "post_id": "a",
            "method": "our_method",
            "valid": True,
            "correct": True,
            "reason": "Off-topic and lacking context",
        },
        {
            "post_id": "a",
            "method": "our_method",
            "valid": True,
            "correct": True,
            "reason": "synthetic style",
        },
        {"post_id": "b", "method": "our_method", "valid": True, "correct": False},
    ]
    result = analyze(paired, judgments)
    assert result["posts_with_tangent_reports"] == 2
    assert result["detected"]["posts"] == 1
    assert result["detected"]["search_share_mean"] == 0.75
    assert result["detected"]["relevance_mean"] == pytest.approx(0.3)
    assert result["not_detected"]["relevance_mean"] == 0.9
    assert result["detected_reason_categories"] == {"off_topic": 1, "style": 1}


def test_analyze_reports_empty_groups_without_inventing_measurements() -> None:
    result = analyze([], [])
    assert result["detected"]["search_share_mean"] is None
    assert result["not_detected"]["relevance_mean"] is None
