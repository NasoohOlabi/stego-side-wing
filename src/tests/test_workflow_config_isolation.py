"""Tests for workflow config isolation (live sim)."""

from pathlib import Path

from workflows.config import WorkflowConfig, get_config, isolated_workflow_config


def test_isolated_workflow_config_overrides_get_config(tmp_path: Path) -> None:
    cfg = WorkflowConfig(
        url_cache_dir=tmp_path / "u",
        research_terms_db_path=tmp_path / "t.db",
        angles_cache_dir=tmp_path / "a",
    )
    assert get_config() is not cfg
    with isolated_workflow_config(cfg):
        assert get_config() is cfg
    assert get_config() is not cfg
