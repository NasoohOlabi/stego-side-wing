import json
from pathlib import Path

import pytest

from workflows.utils.output_results_shape import (
    OutputResultsShapeKind,
    assert_valid_n8n_stego_artifact,
    classify_output_results_root,
    migrate_output_results_file,
    n8n_save_object_body,
)


def test_classify_ok_n8n_array() -> None:
    data = [{"stegoText": "x", "embedding": {}, "post": {"id": "1"}}]
    assert classify_output_results_root(data) is OutputResultsShapeKind.OK


def test_classify_ok_rejects_extra_keys() -> None:
    data = [{"stegoText": "x", "embedding": {}, "post": {}, "extra": 1}]
    assert classify_output_results_root(data) is OutputResultsShapeKind.OTHER


def test_classify_migratable_flat_dict() -> None:
    data = {"stego_text": "hi", "post": {}, "embedding": {}, "succeeded": True}
    assert classify_output_results_root(data) is OutputResultsShapeKind.MIGRATABLE


def test_classify_other_empty_list() -> None:
    assert classify_output_results_root([]) is OutputResultsShapeKind.OTHER


def test_migrate_file_dry_run_and_apply(tmp_path: Path) -> None:
    flat = {"stego_text": "secret", "embedding": {"a": 1}, "post": {"id": "p"}}
    p = tmp_path / "x.json"
    p.write_text(json.dumps(flat), encoding="utf-8")

    assert migrate_output_results_file(p, apply=False) == "would_migrate"
    assert json.loads(p.read_text(encoding="utf-8")) == flat

    assert migrate_output_results_file(p, apply=True) == "migrated"
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded == n8n_save_object_body(flat)

    assert migrate_output_results_file(p, apply=False) == "ok"


def test_migrate_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert migrate_output_results_file(p, apply=False) == "error"


def test_migrate_other_shape_skipped(tmp_path: Path) -> None:
    p = tmp_path / "o.json"
    p.write_text('{"foo": 1}', encoding="utf-8")
    assert migrate_output_results_file(p, apply=False) == "other"


def test_assert_valid_n8n_stego_artifact_accepts_non_empty_stego() -> None:
    data = [{"stegoText": "ok", "embedding": {}, "post": {"id": "1"}}]
    assert_valid_n8n_stego_artifact(data)


def test_assert_valid_n8n_stego_artifact_rejects_empty_stego() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        assert_valid_n8n_stego_artifact([{"stegoText": "", "embedding": {}, "post": {}}])
