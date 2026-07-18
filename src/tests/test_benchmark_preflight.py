from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("benchmark_preflight", ROOT / "scripts" / "benchmark_preflight.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_manifest_rejects_duplicate_post_ids(tmp_path: Path) -> None:
    models = tmp_path / "models.json"
    protocol = tmp_path / "protocol.json"
    models.write_text('{"models":[{"id":"local/test","required":true}]}', encoding="utf-8")
    protocol.write_text('{"payload_bits":[32]}', encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        MODULE.build_manifest(["a", "a"], models, protocol)


def test_build_manifest_contains_hashes(tmp_path: Path) -> None:
    models = tmp_path / "models.json"
    protocol = tmp_path / "protocol.json"
    models.write_text('{"models":[{"id":"local/test","required":true}]}', encoding="utf-8")
    protocol.write_text('{"payload_bits":[32]}', encoding="utf-8")
    manifest = MODULE.build_manifest(["a", "b"], models, protocol)
    assert manifest["post_ids"] == ["a", "b"]
    assert len(manifest["model_manifest_sha256"]) == 64
    assert manifest["required_model_ids"] == ["local/test"]
    assert manifest["payload_assignments"][0]["payload_bits"] == 64
    assert len(manifest["payload_assignments"][0]["payload_sha256"]) == 64


def test_build_manifest_hashes_every_frozen_post_and_angle(tmp_path: Path) -> None:
    models = tmp_path / "models.json"
    protocol = tmp_path / "protocol.json"
    angles = tmp_path / "angles"
    dataset = tmp_path / "dataset"
    angles.mkdir()
    dataset.mkdir()
    models.write_text('{"models":[{"id":"local/test","required":true}]}', encoding="utf-8")
    protocol.write_text('{"payload_bits":[64]}', encoding="utf-8")
    (angles / "a.json").write_text('{"angles":[]}', encoding="utf-8")
    (dataset / "a.json").write_text('{"id":"a"}', encoding="utf-8")

    manifest = MODULE.build_manifest(
        ["a"], models, protocol, angles_dir=angles, dataset_dir=dataset
    )

    assert len(manifest["angle_artifact_sha256"]["a"]) == 64
    assert len(manifest["dataset_artifact_sha256"]["a"]) == 64
