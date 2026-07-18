from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts/prepare_tangent_db_comparison.py"
    spec = importlib.util.spec_from_file_location("prepare_tangent_comparison", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary(*, passed: bool = True, minimum: float = 1.0) -> dict:
    return {
        "diversity_guard": {"passed": passed, "minimum_ratio": minimum},
        "tangent_db_quality": {"version": "tangent_db_quality_summary_v1", "unique_posts": 2},
    }


def test_init_creates_separate_legacy_and_v1_manifests(tmp_path: Path) -> None:
    module = _module()
    contract_path = module.initialize_comparison(tmp_path, comparison_id="cmp")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract["live_provider_calls_started"] is False
    assert contract["minimum_diversity_ratio"] == 1.0
    for lane in ("legacy", "v1"):
        manifest = json.loads((tmp_path / lane / "prep_run.json").read_text(encoding="utf-8"))
        assert manifest["tangent_db_builder"] == lane
        assert Path(manifest["dataset_root"]) == (tmp_path / lane).resolve()


def test_finalize_merges_quality_summaries(tmp_path: Path) -> None:
    module = _module()
    module.initialize_comparison(tmp_path, comparison_id="cmp")
    legacy = tmp_path / "legacy-summary.json"
    v1 = tmp_path / "v1-summary.json"
    legacy.write_text(json.dumps(_summary()), encoding="utf-8")
    v1.write_text(json.dumps(_summary()), encoding="utf-8")

    output = module.finalize_comparison(tmp_path, legacy, v1)
    merged = json.loads(output.read_text(encoding="utf-8"))

    assert set(merged["tangent_db_quality_summaries"]) == {"legacy", "v1"}
    assert merged["tangent_db_comparison"]["comparison_id"] == "cmp"


def test_finalize_rejects_lane_that_fails_m9(tmp_path: Path) -> None:
    module = _module()
    module.initialize_comparison(tmp_path, comparison_id="cmp")
    legacy = tmp_path / "legacy-summary.json"
    v1 = tmp_path / "v1-summary.json"
    legacy.write_text(json.dumps(_summary(passed=False)), encoding="utf-8")
    v1.write_text(json.dumps(_summary()), encoding="utf-8")

    try:
        module.finalize_comparison(tmp_path, legacy, v1)
    except ValueError as exc:
        assert "M9" in str(exc)
    else:
        raise AssertionError("expected M9 validation failure")
