"""Tests for WORKFLOW_DATASET_ROOT step-dir rebasing (prepared-posts isolation)."""

from pathlib import Path

import pytest

from infrastructure import config as cfg
from infrastructure.prep_run_manifest import (
    build_prep_run_manifest,
    write_prep_run_manifest,
)
from services.posts_service import list_posts

ALL_STEPS = [
    "filter-url-unresolved",
    "filter-researched",
    "angles-step",
    "final-step",
]


@pytest.fixture(autouse=True)
def _clear_dataset_env(monkeypatch):
    monkeypatch.delenv("WORKFLOW_DATASET_ROOT", raising=False)
    monkeypatch.delenv("WORKFLOW_DATASET_SEED_GLOBAL", raising=False)


def test_unset_root_is_byte_identical_to_global_defaults(monkeypatch):
    # Snapshot the global mapping, then confirm the resolver reproduces it exactly.
    for step in ALL_STEPS:
        source, dest = cfg.get_step_dirs(step)
        assert source == cfg.resolve_path(cfg.STEPS[step]["source_dir"])
        assert dest == cfg.resolve_path(cfg.STEPS[step]["dest_dir"])


def test_root_rebases_every_step_dir_by_leaf_name(monkeypatch):
    monkeypatch.setenv("WORKFLOW_DATASET_ROOT", "datasets/prep_runs/v1_test")
    root = cfg.REPO_ROOT / "datasets" / "prep_runs" / "v1_test"

    assert cfg.get_step_dirs("angles-step") == (
        root / "news_researched",
        root / "news_angles",
    )
    assert cfg.get_step_dirs("final-step") == (
        root / "news_angles",
        root / "output-results",
    )
    assert cfg.get_step_dirs("filter-researched") == (
        root / "news_url_fetched",
        root / "news_researched",
    )


def test_posts_listing_uses_rebased_step_dirs(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WORKFLOW_DATASET_ROOT", str(tmp_path))
    source = tmp_path / "news_researched"
    (tmp_path / "news_angles").mkdir()
    source.mkdir()
    (source / "isolated.json").write_text("{}", encoding="utf-8")

    assert list_posts(count=1, step="angles-step") == {
        "fileNames": ["isolated.json"]
    }


def test_seed_corpus_stays_global_by_default(monkeypatch):
    monkeypatch.setenv("WORKFLOW_DATASET_ROOT", "datasets/prep_runs/v1_test")
    root = cfg.REPO_ROOT / "datasets" / "prep_runs" / "v1_test"

    source, dest = cfg.get_step_dirs("filter-url-unresolved")
    # Seed (news_cleaned) source stays global; its destination is rebased.
    assert source == cfg.resolve_path(cfg.POSTS_DIRECTORY)
    assert dest == root / "news_url_fetched"


def test_seed_can_be_rebased_when_seed_global_disabled(monkeypatch):
    monkeypatch.setenv("WORKFLOW_DATASET_ROOT", "datasets/prep_runs/v1_test")
    monkeypatch.setenv("WORKFLOW_DATASET_SEED_GLOBAL", "false")
    root = cfg.REPO_ROOT / "datasets" / "prep_runs" / "v1_test"

    source, _ = cfg.get_step_dirs("filter-url-unresolved")
    assert source == root / "news_cleaned"


def test_absolute_root_is_honored(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WORKFLOW_DATASET_ROOT", str(tmp_path))
    source, dest = cfg.get_step_dirs("angles-step")
    assert source == tmp_path / "news_researched"
    assert dest == tmp_path / "news_angles"


def test_empty_root_falls_back_to_global(monkeypatch):
    monkeypatch.setenv("WORKFLOW_DATASET_ROOT", "   ")
    assert cfg.get_workflow_dataset_root() is None
    source, dest = cfg.get_step_dirs("angles-step")
    assert source == cfg.resolve_path(cfg.STEPS["angles-step"]["source_dir"])
    assert dest == cfg.resolve_path(cfg.STEPS["angles-step"]["dest_dir"])


def test_prep_manifest_requires_isolated_root():
    with pytest.raises(ValueError, match="WORKFLOW_DATASET_ROOT"):
        build_prep_run_manifest(run_id="v1_test")


def test_prep_manifest_records_recipe(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WORKFLOW_DATASET_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_TANGENT_DB_BUILDER", "v1")
    manifest = build_prep_run_manifest(run_id="v1_test", notes="test run")

    assert manifest.run_id == "v1_test"
    assert manifest.tangent_db_builder == "v1"
    assert manifest.tangent_db_config_hash
    assert manifest.dataset_root == str(tmp_path)
    assert manifest.step_dirs["angles-step"]["dest"] == str(tmp_path / "news_angles")

    path = write_prep_run_manifest(run_id="v1_test", notes="test run")
    assert path == tmp_path / "prep_run.json"
    assert '"run_id": "v1_test"' in path.read_text(encoding="utf-8")
