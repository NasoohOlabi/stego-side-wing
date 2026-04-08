import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger

from workflows.contracts import FetchUrlResult
from workflows.pipelines.research import ResearchPipeline
from workflows.utils.research_relevance_debug import (
    jaccard,
    research_debug_log_dir,
    term_overlap_metrics,
    tokenize,
    write_research_results_debug,
    write_research_terms_debug,
)


def test_tokenize_basic():
    assert "hello" in tokenize("Hello, world!")
    assert "world" in tokenize("Hello, world!")


def test_jaccard_empty_and_overlap():
    empty: frozenset[str] = frozenset()
    assert jaccard(empty, empty) == 1.0
    assert jaccard(empty, frozenset({"a"})) == 0.0
    a, b = frozenset({"a", "b"}), frozenset({"b", "c"})
    assert jaccard(a, b) == pytest.approx(1 / 3)


def test_term_overlap_metrics_respects_corpus():
    corpus = frozenset({"python", "async", "io"})
    title = frozenset({"python"})
    m = term_overlap_metrics("python", corpus_tokens=corpus, title_tokens=title)
    assert m["term_char_len"] == 6
    assert m["jaccard_vs_title_only"] == pytest.approx(1.0)
    assert m["jaccard_vs_post_corpus"] == pytest.approx(0.3333, rel=1e-3)


def test_write_terms_and_results_jsonl(tmp_path: Path):
    trace_id = "t1"
    terms_report = {"used_cache": True, "cache_hit": False, "parse_mode": "json_array"}
    write_research_terms_debug(
        log_dir=tmp_path,
        trace_id=trace_id,
        post_id="p1",
        search_terms=["alpha beta", "gamma"],
        terms_report=terms_report,
        post_title="alpha story",
        post_text="beta delta",
    )
    p_terms = tmp_path / "research_terms.jsonl"
    assert p_terms.is_file()
    line = json.loads(p_terms.read_text(encoding="utf-8").strip())
    for key in ("timestamp", "level", "component", "trace_id", "message"):
        assert key in line
    assert line["component"] == "ResearchRelevanceDebug"
    assert line["trace_id"] == trace_id
    assert line["message"] == "research_debug_terms"
    assert len(line["per_term_metrics"]) == 2

    corpus_tokens = tokenize("alpha story\nbeta delta")
    write_research_results_debug(
        log_dir=tmp_path,
        trace_id=trace_id,
        post_id="p1",
        search_terms=["q1"],
        raw_results_by_term=[
            [
                {
                    "link": "https://www.example.com/x",
                    "title": "alpha guide",
                    "snippet": "beta tips",
                }
            ]
        ],
        corpus_tokens=corpus_tokens,
        raw_hits_total=1,
        selected_unique_urls=1,
    )
    p_res = tmp_path / "research_results.jsonl"
    lines = [json.loads(x) for x in p_res.read_text(encoding="utf-8").strip().splitlines()]
    assert len(lines) == 1
    res_line = lines[0]
    assert res_line["message"] == "research_debug_results"
    assert res_line["hits"][0]["domain"] == "example.com"
    assert res_line["mean_snippet_vs_corpus_jaccard"] >= 0


def test_research_debug_log_dir_unset(monkeypatch):
    monkeypatch.delenv("RESEARCH_DEBUG_LOG_DIR", raising=False)
    assert research_debug_log_dir() is None


def test_research_debug_log_dir_set(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RESEARCH_DEBUG_LOG_DIR", str(tmp_path))
    assert research_debug_log_dir() == tmp_path.resolve()


def _research_pipeline_stub() -> ResearchPipeline:
    p = ResearchPipeline.__new__(ResearchPipeline)
    p._log = logger.bind(component="ResearchPipeline")
    return p


def test_preview_post_writes_debug_files_when_env_set(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RESEARCH_DEBUG_LOG_DIR", str(tmp_path))
    pipeline = _research_pipeline_stub()
    pipeline.gen_terms = SimpleNamespace(
        preview_generation=lambda **kwargs: {
            "terms": ["term1"],
            "used_cache": False,
            "cache_hit": False,
            "parse_mode": "json_array",
        }
    )

    def google_search(query, first, count):
        return {
            "results": [
                {
                    "link": "https://a.com/page",
                    "title": "t",
                    "snippet": "s",
                }
            ]
        }

    pipeline.backend = SimpleNamespace(google_search=google_search)
    pipeline.fetch_content = SimpleNamespace(
        fetch=lambda url, use_cache: FetchUrlResult(url=url, success=True, text="x")
    )
    post = {"id": "p1", "title": "hello", "selftext": "world", "url": "https://origin"}
    pipeline.preview_post(post)
    assert (tmp_path / "research_terms.jsonl").is_file()
    assert (tmp_path / "research_results.jsonl").is_file()


def test_append_jsonl_not_called_when_debug_dir_none(monkeypatch):
    monkeypatch.delenv("RESEARCH_DEBUG_LOG_DIR", raising=False)
    calls: list[Path] = []

    def fake_append(path: Path, payload: dict) -> None:
        calls.append(path)

    monkeypatch.setattr(
        "workflows.pipelines.research.research_debug_log_dir",
        lambda: None,
    )
    pipeline = _research_pipeline_stub()
    pipeline.gen_terms = SimpleNamespace(
        preview_generation=lambda **kwargs: {"terms": ["t"], "cache_hit": False}
    )
    pipeline.backend = SimpleNamespace(
        google_search=lambda **kw: {"results": [{"link": "https://x.com", "title": "", "snippet": ""}]}
    )
    pipeline.fetch_content = SimpleNamespace(
        fetch=lambda url, use_cache: FetchUrlResult(url=url, success=True, text=".")
    )
    monkeypatch.setattr(
        "workflows.utils.research_relevance_debug._append_jsonl",
        fake_append,
    )
    post = {"id": "p2", "title": "a", "selftext": "b"}
    pipeline.preview_post(post)
    assert calls == []
