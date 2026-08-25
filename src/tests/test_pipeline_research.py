from types import SimpleNamespace

import pytest
from loguru import logger

from workflows.contracts import FetchUrlResult
from workflows.pipelines.research import ResearchPipeline


def _research_pipeline_stub() -> ResearchPipeline:
    p = ResearchPipeline.__new__(ResearchPipeline)
    p._log = logger.bind(component="ResearchPipeline")
    p.last_research_breakdown_posts = []
    return p


@pytest.mark.parametrize(
    ("post", "expected"),
    [
        ({}, True),
        ({"search_results": []}, True),
        ({"search_results": ["", "   "]}, True),
        ({"search_results": {"a": "", "b": ["", "x"]}}, False),
        ({"search_results": ["useful"]}, False),
    ],
)
def test_is_new_post_variants(post, expected):
    assert ResearchPipeline._is_new_post(post) is expected


def test_research_post_requires_id():
    pipeline = _research_pipeline_stub()
    with pytest.raises(ValueError, match="must have 'id' field"):
        pipeline.research_post({})


def test_research_post_skips_when_already_researched():
    post = {"id": "p1", "search_results": ["exists"]}
    pipeline = _research_pipeline_stub()
    pipeline.gen_terms = SimpleNamespace(
        generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("should not run"))
    )
    pipeline.backend = SimpleNamespace()
    pipeline.fetch_content = SimpleNamespace()

    assert pipeline.research_post(post) is post


def test_research_post_builds_deduped_non_pdf_results():
    pipeline = _research_pipeline_stub()
    pipeline.gen_terms = SimpleNamespace(
        preview_generation=lambda **kwargs: {"terms": ["term1", "term2"]}
    )

    def google_search(query, first, count):
        if query == "term1":
            return {
                "results": [
                    {"link": "https://a.com/page"},
                    {"link": "https://a.com/page"},  # duplicate
                    {"link": "https://doc.pdf"},  # skipped
                ]
            }
        return {"results": [{"link": "https://b.com/page"}]}

    pipeline.backend = SimpleNamespace(google_search=google_search)
    pipeline.fetch_content = SimpleNamespace(
        fetch=lambda url, use_cache: FetchUrlResult(url=url, success=True, text=f"text:{url}")
    )

    post = {"id": "p1", "title": "t", "selftext": "body", "url": "https://origin"}
    result = pipeline.research_post(post)

    assert result["search_results"] == [
        "text:https://a.com/page",
        "text:https://b.com/page",
    ]
    preview = pipeline.preview_post(post)
    timing = preview["report"]["timing"]
    assert timing["trace_id"]
    assert timing["preview_total_ms"] >= 0
    assert timing["terms_phase_ms"] >= 0
    assert timing["search_phase_ms"] >= 0
    assert timing["fetch_phase_ms"] >= 0


def test_research_post_uses_title_when_term_generation_fails():
    pipeline = _research_pipeline_stub()
    pipeline.gen_terms = SimpleNamespace(
        preview_generation=lambda **kwargs: {"terms": [], "error": "provider blocked"}
    )
    queries = []

    def google_search(query, first, count):
        queries.append(query)
        return {"results": [{"link": "https://example.com/story"}]}

    pipeline.backend = SimpleNamespace(google_search=google_search)
    pipeline.fetch_content = SimpleNamespace(
        fetch=lambda url, use_cache: FetchUrlResult(url=url, success=True, text="source text")
    )

    result = pipeline.research_post(
        {"id": "p-title", "title": "Fallback title query", "selftext": "body"}
    )

    assert queries == ["Fallback title query"]
    assert result["search_results"] == ["source text"]


def test_process_posts_saves_local_for_all_and_remote_for_new_only():
    local_saves = []
    remote_saves = []
    posts = {
        "new.json": {"id": "new", "search_results": []},
        "old.json": {"id": "old", "search_results": ["existing"]},
    }

    pipeline = _research_pipeline_stub()
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": ["new.json", "old.json"]},
        get_post_local=lambda file_name, step: dict(posts[file_name]),
        save_post_local=lambda post, step: local_saves.append(post["id"]),
        save_post=lambda post, step: remote_saves.append(post["id"]),
    )

    def _pair(post, step, **kwargs):
        return {**post, "processed": True}, {"post_id": post["id"], "stub": True}

    pipeline._research_post_pair = _pair

    result = pipeline.process_posts(step="filter-researched", count=2, offset=0)

    assert [p["id"] for p in result] == ["new", "old"]
    assert local_saves == ["new", "old"]
    assert remote_saves == ["new"]


def test_process_post_objects_skips_failed_post_and_continues_batch():
    """A single post's research failure must not abort the rest of the batch."""
    saved = []
    pipeline = _research_pipeline_stub()
    pipeline.backend = SimpleNamespace(
        save_post_local=lambda post, step: saved.append(post["id"]),
        save_post=lambda *a, **k: None,
    )

    def _pair(post, step, **kwargs):
        if post["id"] == "bad":
            raise RuntimeError("web search failed")
        return {**post, "processed": True}, {"post_id": post["id"]}

    pipeline._research_post_pair = _pair

    posts = [
        {"id": "good1", "search_results": []},
        {"id": "bad", "search_results": []},
        {"id": "good2", "search_results": []},
    ]
    result = pipeline.process_post_objects(posts=posts, step="filter-researched")

    assert [p["id"] for p in result] == ["good1", "good2"]
    assert saved == ["good1", "good2"]


def test_process_post_objects_include_breakdown_appends_reports():
    pipeline = _research_pipeline_stub()
    pipeline.gen_terms = SimpleNamespace(preview_generation=lambda **kwargs: {"terms": ["t1"]})
    pipeline.backend = SimpleNamespace(
        save_post_local=lambda *a, **k: None,
        save_post=lambda *a, **k: None,
        google_search=lambda **kwargs: {"results": [{"link": "https://a.com/x"}]},
    )
    pipeline.fetch_content = SimpleNamespace(
        fetch=lambda url, use_cache: FetchUrlResult(url=url, success=True, text=f"body:{url}")
    )
    posts = [{"id": "p1", "title": "t", "selftext": "b", "url": "https://u"}]
    pipeline.process_post_objects(posts=posts, step="filter-researched", include_breakdown=True)
    assert len(pipeline.last_research_breakdown_posts) == 1
    entry = pipeline.last_research_breakdown_posts[0]
    assert entry["post_id"] == "p1"
    assert "timing" in entry["report"]
    assert entry["report"]["timing"]["preview_total_ms"] >= 0


def test_process_posts_clears_breakdown_when_include_breakdown():
    pipeline = _research_pipeline_stub()
    pipeline.last_research_breakdown_posts = [{"stale": True}]
    pipeline.backend = SimpleNamespace(
        posts_list=lambda **kwargs: {"fileNames": []},
    )
    pipeline.process_posts(step="filter-researched", count=1, offset=0, include_breakdown=True)
    assert pipeline.last_research_breakdown_posts == []


def test_preview_post_caps_selected_urls(monkeypatch, clear_workflow_capacity_env):
    pipeline = _research_pipeline_stub()
    monkeypatch.setenv("WORKFLOW_RESEARCH_MAX_SELECTED_URLS", "2")
    pipeline.gen_terms = SimpleNamespace(
        preview_generation=lambda **kwargs: {
            "terms": ["term1", "term2"],
            "terms_capped": False,
            "max_terms": 8,
        }
    )

    def google_search(query, first, count):
        if query == "term1":
            return {
                "results": [
                    {"link": "https://a.com/1"},
                    {"link": "https://a.com/2"},
                ]
            }
        return {
            "results": [
                {"link": "https://b.com/1"},
            ]
        }

    pipeline.backend = SimpleNamespace(google_search=google_search)
    pipeline.fetch_content = SimpleNamespace(
        fetch=lambda url, use_cache: FetchUrlResult(url=url, success=True, text=f"text:{url}")
    )

    preview = pipeline.preview_post(
        {"id": "p-cap", "title": "t", "selftext": "body", "url": "https://origin"}
    )

    assert preview["post"]["search_results"] == [
        "text:https://a.com/1",
        "text:https://a.com/2",
    ]
    assert preview["report"]["capacity"]["selected_url_cap_hit"] is True
    assert preview["report"]["capacity"]["max_selected_urls"] == 2


def test_web_search_google_or_bing_raises_quota_when_bing_fallback_disabled(monkeypatch):
    pipeline = _research_pipeline_stub()
    pipeline.backend = SimpleNamespace(
        google_search=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("429 quota exceeded"))
    )

    def _should_not_use_bing(**kwargs):
        raise AssertionError("bing fallback should be disabled")

    monkeypatch.setattr("services.search_service.search_bing", _should_not_use_bing)
    monkeypatch.setattr("services.search_service.search_bing_news_rss", _should_not_use_bing)
    monkeypatch.setattr("services.search_service.search_google_news_rss", _should_not_use_bing)
    monkeypatch.setattr("services.search_service.search_yahoo_news", _should_not_use_bing)
    monkeypatch.setattr("services.search_service.search_duckduckgo", _should_not_use_bing)

    with pytest.raises(RuntimeError, match="quota"):
        pipeline._web_search_google_or_bing(
            query="term",
            first=1,
            count=10,
            post_id="p1",
            disable_bing_fallback=True,
        )


def test_web_search_falls_back_to_duckduckgo_on_google_quota(monkeypatch):
    pipeline = _research_pipeline_stub()
    pipeline.backend = SimpleNamespace(
        google_search=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("429 quota exceeded"))
    )
    monkeypatch.setattr(
        "services.search_service.search_duckduckgo",
        lambda **kwargs: {"results": [{"title": "term", "link": "https://x", "snippet": "s"}]},
    )

    def _should_not_use_bing(**kwargs):
        raise AssertionError("bing should not run when duckduckgo returns results")

    monkeypatch.setattr("services.search_service.search_bing", _should_not_use_bing)
    monkeypatch.setattr("services.search_service.search_bing_news_rss", _should_not_use_bing)
    monkeypatch.setattr("services.search_service.search_google_news_rss", _should_not_use_bing)
    monkeypatch.setattr("services.search_service.search_yahoo_news", _should_not_use_bing)
    out = pipeline._web_search_google_or_bing(
        query="term", first=1, count=10, post_id="p1", disable_bing_fallback=False
    )
    assert out["results"][0]["link"] == "https://x"


def test_web_search_uses_bing_rss_before_metered_bing(monkeypatch):
    pipeline = _research_pipeline_stub()
    pipeline.backend = SimpleNamespace(
        google_search=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("429 quota exceeded"))
    )
    monkeypatch.setattr("services.search_service.search_duckduckgo", lambda **kwargs: {"results": []})
    monkeypatch.setattr("services.search_service.search_yahoo_news", lambda **kwargs: {"results": []})
    monkeypatch.setattr(
        "services.search_service.search_bing_news_rss",
        lambda **kwargs: {"results": [{"title": "term", "link": "https://rss", "snippet": "s"}]},
    )
    monkeypatch.setattr("services.search_service.search_google_news_rss", lambda **kwargs: {"results": []})
    monkeypatch.setattr(
        "services.search_service.search_bing",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("metered Bing should not run")),
    )

    out = pipeline._web_search_google_or_bing(
        query="term", first=1, count=10, post_id="p1", disable_bing_fallback=False
    )

    assert out["results"][0]["link"] == "https://rss"


def test_web_search_uses_google_news_when_bing_news_is_empty(monkeypatch):
    pipeline = _research_pipeline_stub()
    pipeline._google_quota_detected = True
    monkeypatch.setattr("services.search_service.search_duckduckgo", lambda **kwargs: {"results": []})
    monkeypatch.setattr("services.search_service.search_yahoo_news", lambda **kwargs: {"results": []})
    monkeypatch.setattr("services.search_service.search_bing_news_rss", lambda **kwargs: {"results": []})
    monkeypatch.setattr(
        "services.search_service.search_google_news_rss",
        lambda **kwargs: {"results": [{"title": "term", "link": "https://news", "snippet": "s"}]},
    )

    out = pipeline._web_search_google_or_bing(
        query="term", first=1, count=10, post_id="p1", disable_bing_fallback=False
    )

    assert out["results"][0]["link"] == "https://news"


def test_web_search_uses_yahoo_news_before_google_news(monkeypatch):
    pipeline = _research_pipeline_stub()
    pipeline._google_quota_detected = True
    monkeypatch.setattr("services.search_service.search_duckduckgo", lambda **kwargs: {"results": []})
    monkeypatch.setattr(
        "services.search_service.search_yahoo_news",
        lambda **kwargs: {"results": [{"title": "term", "link": "https://yahoo", "snippet": "s"}]},
    )
    monkeypatch.setattr(
        "services.search_service.search_google_news_rss",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Google News should not run")),
    )

    out = pipeline._web_search_google_or_bing(
        query="term", first=1, count=10, post_id="p1", disable_bing_fallback=False
    )

    assert out["results"][0]["link"] == "https://yahoo"


def test_fallback_search_rejects_lexically_unrelated_results(monkeypatch):
    pipeline = _research_pipeline_stub()
    pipeline._google_quota_detected = True
    monkeypatch.setattr(
        "services.search_service.search_duckduckgo",
        lambda **kwargs: {"results": [{"title": "Business branding elements", "link": "x"}]},
    )
    monkeypatch.setattr("services.search_service.search_yahoo_news", lambda **kwargs: {"results": []})
    monkeypatch.setattr(
        "services.search_service.search_google_news_rss",
        lambda **kwargs: {
            "results": [{"title": "Aalborg Zoo accepts donated pets", "link": "https://news"}]
        },
    )

    out = pipeline._web_search_google_or_bing(
        query="Aalborg Zoo donated pets", first=1, count=10, post_id="p1"
    )

    assert out["results"][0]["link"] == "https://news"


def test_fallback_search_rejects_generic_heading_query():
    pipeline = _research_pipeline_stub()

    assert pipeline._relevant_fallback_results(
        "* Key Elements:", [{"title": "Key elements for successful branding"}]
    ) == []


def test_google_quota_opens_circuit_for_later_queries(monkeypatch):
    pipeline = _research_pipeline_stub()
    calls = []

    def google_search(**kwargs):
        calls.append(kwargs["query"])
        raise RuntimeError("429 quota exceeded")

    pipeline.backend = SimpleNamespace(google_search=google_search)
    monkeypatch.setattr("services.search_service.search_duckduckgo", lambda **kwargs: {"results": []})
    monkeypatch.setattr("services.search_service.search_yahoo_news", lambda **kwargs: {"results": []})
    monkeypatch.setattr(
        "services.search_service.search_bing_news_rss",
        lambda **kwargs: {
            "results": [{"title": kwargs["query"], "link": "https://rss", "snippet": "s"}]
        },
    )
    monkeypatch.setattr("services.search_service.search_google_news_rss", lambda **kwargs: {"results": []})

    for query in ("first", "second"):
        pipeline._web_search_google_or_bing(
            query=query, first=1, count=10, post_id="p1", disable_bing_fallback=False
        )

    assert calls == ["first"]
