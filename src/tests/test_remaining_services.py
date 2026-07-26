"""Direct coverage for the service modules previously exercised only through routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import (
    kv_service,
    recent_updates_service,
    state_service,
    stego_benchmark_service,
    stego_experiment_service,
)
from services.workflow_backend_client import LocalBackendClient


def test_recent_updates_parses_numstat_and_filters_generated_paths() -> None:
    lines = [
        "__COMMIT__",
        "full",
        "short",
        "Author",
        "2026-01-01T00:00:00+00:00",
        "Subject",
        "3\t1\tsrc/app.py",
        "2\t0\tdatasets/generated.json",
    ]
    commits = recent_updates_service._parse_commits(lines, 10)
    assert commits[0].files_changed_total == 2
    assert commits[0].paths == ["src/app.py"]
    assert commits[0].generated_files_changed == 1


def test_state_service_json_round_trip_inside_repo_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(state_service, "REPO_ROOT", tmp_path)
    written = state_service.write_json_file("state/sample.json", {"ok": True})
    assert written["written"] is True
    assert state_service.read_json_file("state/sample.json")["data"] == {"ok": True}
    assert state_service.delete_path("state/sample.json")["deleted"] is True


def test_state_service_rejects_parent_traversal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(state_service, "REPO_ROOT", tmp_path)
    with pytest.raises(ValueError, match="inside repository root"):
        state_service.safe_repo_path("../outside.json")


def test_kv_service_round_trip(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "kv.db"
    monkeypatch.setattr(kv_service, "DB_FILE", str(db_path))
    kv_service.init_db()
    kv_service.set_value("alpha", {"value": 1})
    assert kv_service.get_value("alpha") == {"k": "alpha", "v": {"value": 1}}
    assert kv_service.list_values()["pagination"]["total"] == 1
    assert kv_service.delete_value("alpha")["deleted"] is True


class _Config:
    def __init__(self, source: Path, destination: Path) -> None:
        self.source = source
        self.destination = destination

    def get_step_dirs(self, _step: str) -> tuple[Path, Path]:
        return self.source, self.destination


def test_workflow_backend_client_local_io(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    (source / "p1.json").write_text(json.dumps({"id": "p1"}), encoding="utf-8")
    client = LocalBackendClient(_Config(source, destination))  # type: ignore[arg-type]
    assert client.get_post_local("p1.json", "step") == {"id": "p1"}
    client.save_post_local({"id": "p2", "body": "x"}, "step")
    assert json.loads((destination / "p2.json").read_text(encoding="utf-8"))["body"] == "x"


def test_benchmark_service_reports_selection_capacity() -> None:
    result = stego_benchmark_service.build_sample_experiment_metrics(
        {
            "embedding": {
                "commentBits": "10",
                "angleBits": "1",
                "compression": {"method": "test", "ratio": 0.5},
            }
        },
        stego_text="visible text",
        payload_bytes=4,
    )
    assert result["selection_metrics"]["selection_bits"] == 3
    assert result["capacity_metrics"]["selection_bits"] == 3


def test_benchmark_service_excludes_modulo_alias_bits_from_capacity() -> None:
    result = stego_benchmark_service.build_sample_experiment_metrics(
        {
            "embedding": {
                "commentBits": "101",
                "angleBits": "11",
                "commentEmbedding": {"bitsCount": 3, "recoverableBitsCount": 2},
                "angleEmbedding": {"bitsCount": 2, "recoverableBitsCount": 1},
            }
        },
        stego_text="visible text",
        payload_bytes=1,
    )

    assert result["selection_metrics"]["physical_selection_bits"] == 5
    assert result["capacity_metrics"]["selection_bits"] == 3
    assert result["capacity_metrics"]["total_bits"] == 3


def test_experiment_service_resolves_builtin_variant(tmp_path: Path) -> None:
    manifest = tmp_path / "variants.json"
    manifest.write_text('{"version":1,"variants":[]}', encoding="utf-8")
    variant = stego_experiment_service.resolve_experiment_variants(
        ["balanced_naturalness_gate"], manifest
    )[0]
    assert variant.base_profile == "balanced"
    assert variant.env_overrides["WORKFLOW_NATURALNESS_GATE_ENABLED"] == "1"
