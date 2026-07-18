from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "analyze_attack_recovery.py"
    spec = importlib.util.spec_from_file_location("analyze_attack_recovery", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_attack_analysis_clusters_repeated_carriers_by_post() -> None:
    module = _load_module()
    rows = [
        {"post_id": "p1", "method": "our_method", "attack": "word_deletion", "severity": 0.1, "applicable": True, "decode_ok": True},
        {"post_id": "p1", "method": "our_method", "attack": "word_deletion", "severity": 0.1, "applicable": True, "decode_ok": False},
        {"post_id": "p2", "method": "our_method", "attack": "word_deletion", "severity": 0.1, "applicable": True, "decode_ok": True},
    ]

    report = module.analyze(rows)

    result = report["attacks"][0]
    assert result["independent_posts"] == 2
    assert result["recovery_rate"] == 0.75
