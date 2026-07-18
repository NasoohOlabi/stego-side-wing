import importlib.util
import json
from pathlib import Path


def _load_script_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_fresh_post_angle_scan.py"
    spec = importlib.util.spec_from_file_location("run_fresh_post_angle_scan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_select_unused_post_id_excludes_latest_e2e_and_angle_scan_post_ids(tmp_path):
    module = _load_script_module()
    dataset = tmp_path / "dataset"
    latest = tmp_path / "e2e" / "latest"
    older = tmp_path / "e2e" / "older"
    scan_root = tmp_path / "angle_scan_runs"
    dataset.mkdir()
    latest.mkdir(parents=True)
    older.mkdir(parents=True)
    (dataset / "used.json").write_text("{}", encoding="utf-8")
    (dataset / "scan-used.json").write_text("{}", encoding="utf-8")
    (dataset / "fresh.json").write_text("{}", encoding="utf-8")
    (older / "post_ids.json").write_text(json.dumps({"post_ids": ["fresh"]}), encoding="utf-8")
    (latest / "post_ids.json").write_text(json.dumps({"post_ids": ["used"]}), encoding="utf-8")
    (scan_root / "run1").mkdir(parents=True)
    (scan_root / "run1" / "angle_scan_summary.json").write_text(
        json.dumps({"post_id": "scan-used"}),
        encoding="utf-8",
    )

    selected = module.select_unused_post_id(dataset, tmp_path / "e2e", scan_root)

    assert selected == "fresh"


def test_build_angle_scan_bits_forces_one_payload_per_angle():
    module = _load_script_module()
    post = {
        "id": "p1",
        "comments": [],
        "angles": [
            {"source_quote": "q0", "tangent": "t0", "category": "c0"},
            {"source_quote": "q1", "tangent": "t1", "category": "c1"},
            {"source_quote": "q2", "tangent": "t2", "category": "c2"},
        ],
    }

    payloads = [module.build_angle_scan_bits(post, idx) for idx in range(3)]

    assert payloads == ["00", "01", "10"]
    assert len(set(payloads)) == 3


def test_summarize_scan_groups_failure_reasons():
    module = _load_script_module()
    rows = [
        {"succeeded": True, "failure_code": "success", "category": "A"},
        {"succeeded": False, "failure_code": "decode_mismatch", "category": "A"},
        {"succeeded": False, "failure_code": "stego_invalid_json", "category": "B"},
        {"succeeded": False, "failure_code": "contextuality_reject", "category": "B"},
    ]

    summary = module.summarize_scan(rows)

    assert summary["total_angles"] == 4
    assert summary["succeeded"] == 1
    assert summary["success_rate"] == 0.25
    assert summary["json_fail_rate"] == 0.25
    assert summary["decode_mismatch_rate"] == 0.25
    assert summary["contextuality_rejection_rate"] == 0.25
