from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch


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


def test_materialize_cached_v1_preserves_input_and_selects_with_source_mix(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    module = _module()
    monkeypatch.setenv("WORKFLOW_CAPACITY_PROFILE", "mid")
    module.initialize_comparison(tmp_path, comparison_id="cmp")
    researched = {
        "id": "post-1",
        "title": "Flood rescue",
        "selftext": "Rescue teams searched the river.",
        "comments": [{"body": "Campers waited for rescue teams."}],
        "search_results": ["River rescue planning requires boats."],
    }
    legacy = dict(
        researched,
        angles=[
            {
                "source_quote": "Campers waited for rescue teams.",
                "tangent": "Campers waited for rescue teams.",
                "category": "Community Discussion",
            },
            {
                "source_quote": "River rescue planning requires boats.",
                "tangent": "River rescue planning requires boats.",
                "category": "Reference Material",
            },
        ],
        options_count=2,
    )
    source = tmp_path / "legacy" / "news_researched" / "post-1.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(researched), encoding="utf-8")
    angles = tmp_path / "legacy" / "news_angles" / "post-1.json"
    angles.parent.mkdir(parents=True)
    angles.write_text(json.dumps(legacy), encoding="utf-8")

    output = module.materialize_cached_v1(tmp_path, post_id="post-1")
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert (tmp_path / "v1" / "news_researched" / "post-1.json").read_bytes() == source.read_bytes()
    assert artifact["options_count"] == len(artifact["angles"]) == 2
    assert artifact["tangent_db_report"]["source_mix_kept"] == {
        "post": 0,
        "comments": 1,
        "search_results": 1,
    }
    assert artifact["tangent_db_report"]["config_hash"] == json.loads(
        (tmp_path / "v1" / "prep_run.json").read_text(encoding="utf-8")
    )["tangent_db_config_hash"]


def test_materialize_luna_uses_real_codec_and_enforces_symmetry(tmp_path: Path) -> None:
    module = _module()
    for lane in ("legacy", "v1"):
        lane_root = tmp_path / lane
        lane_root.mkdir()
        post = {
            "id": "p1",
            "title": "A title with enough words",
            "selftext": "A body with enough words for the codec dictionary.",
            "comments": [{"id": "c1", "body": "An existing parent comment."}],
            "angles": [
                {"tangent": "First distinct angle", "source_quote": "first"},
                {"tangent": "Second distinct angle", "source_quote": "second"},
            ],
            "tangent_db_report": {"config_hash": "x"},
        }
        angles = lane_root / "news_angles"
        angles.mkdir()
        (angles / "p1.json").write_text(json.dumps(post), encoding="utf-8")
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps({"legacy": ["L one", "L two"], "v1": ["V one", "V two"]}), encoding="utf-8")

    module.materialize_luna_comments(tmp_path, post_id="p1", comments_path=comments)

    for lane in ("legacy", "v1"):
        rows = [json.loads(line) for line in (tmp_path / lane / "paired_rows.jsonl").read_text(encoding="utf-8").splitlines()]
        summary = json.loads((tmp_path / lane / "summary.json").read_text(encoding="utf-8"))
        assert [row["selected_angle_index"] for row in rows] == [0, 1]
        assert all(row["decode_ok"] for row in rows)
        assert summary["round_trips_passed"] == 2
        assert summary["diversity_guard"]["ratio"] == 1.0
