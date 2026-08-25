"""Tests for the LUCID TangentsDB-v1 pilot scaffold script."""

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_lucid_tangents_db_pilot.py"
    spec = importlib.util.spec_from_file_location("prepare_lucid_tangents_db_pilot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_initialize_pilot_writes_contract(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.chdir(tmp_path)
    # prep manifest writes under WORKFLOW_DATASET_ROOT / metrics paths; keep isolated
    root = tmp_path / "pilot_root"
    path = module.initialize_pilot(root, pilot_id="t1", notes="unit")
    assert path.is_file()
    contract = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert contract["version"] == "lucid_tangents_db_pilot_v1"
    assert contract["builder"] == "lucid"
    assert contract["artifact_namespace"] == "project_lucid/tangents_db/v1"
    assert (root / "lucid").is_dir()
