"""Tests for workflow config isolation (live sim)."""

import json
from pathlib import Path

from content_acquisition.headless_browser_analyzer import deterministic_hash_sha256, normalize_url
from workflows.adapters.content import ContentAdapter
from workflows.config import WorkflowConfig, get_config, isolated_workflow_config
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline


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


def test_content_adapter_reads_url_cache_from_get_config(tmp_path: Path) -> None:
    cfg = WorkflowConfig(
        url_cache_dir=tmp_path / "u",
        research_terms_db_path=tmp_path / "t.db",
        angles_cache_dir=tmp_path / "a",
    )
    url = "https://example.com/cache-key-x"
    cache_key = deterministic_hash_sha256(normalize_url(url))
    with isolated_workflow_config(cfg):
        cache_file = get_config().url_cache_dir / f"{cache_key}.json"
        cache_file.write_text(
            json.dumps({"result": {"text": "cached-body", "success": True}}),
            encoding="utf-8",
        )
        adapter = ContentAdapter()
        got = adapter._get_cached_content(url)
        assert got is not None
        assert got.success
        assert got.text == "cached-body"


def test_gen_search_terms_syncs_cache_db_under_isolated_config(tmp_path: Path) -> None:
    cfg_a = WorkflowConfig(
        url_cache_dir=tmp_path / "ua",
        research_terms_db_path=tmp_path / "ta.db",
        angles_cache_dir=tmp_path / "aa",
    )
    cfg_b = WorkflowConfig(
        url_cache_dir=tmp_path / "ub",
        research_terms_db_path=tmp_path / "tb.db",
        angles_cache_dir=tmp_path / "ab",
    )
    g = GenSearchTermsPipeline()
    with isolated_workflow_config(cfg_a):
        g._sync_research_terms_cache_binding()
        assert Path(g.cache_db_path).resolve() == cfg_a.research_terms_db_path.resolve()
    with isolated_workflow_config(cfg_b):
        g._sync_research_terms_cache_binding()
        assert Path(g.cache_db_path).resolve() == cfg_b.research_terms_db_path.resolve()
