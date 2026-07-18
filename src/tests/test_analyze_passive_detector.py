from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "analyze_passive_detector.py"
    spec = importlib.util.spec_from_file_location("analyze_passive_detector", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auc_handles_ties() -> None:
    module = _load_module()

    assert module._auc([1, 1, 0, 0], [1.0, 0.0, 0.0, -1.0]) == 0.875


def test_detector_groups_cross_validation_by_post() -> None:
    module = _load_module()
    rows = [
        {
            "post_id": f"p{index}",
            "method": "our_method",
            "accepted": True,
            "stegotexts": ["encoded artificial carrier pattern"],
            "human_texts": ["ordinary community discussion"],
        }
        for index in range(10)
    ]

    report = module.analyze(rows, "our_method", folds=5)

    assert report["independent_post_clusters"] == 10
    assert report["examples"] == 20
    assert report["roc_auc"] is not None
