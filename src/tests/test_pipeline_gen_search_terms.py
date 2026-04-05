from types import SimpleNamespace

from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline


def test_parse_terms_handles_json_and_markdown_fences():
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    parsed = pipeline._parse_terms("```json\n[\"a\", \"b\"]\n```")
    assert parsed == ["a", "b"]


def test_parse_terms_extracts_embedded_array():
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    parsed = pipeline._parse_terms("prefix text [\"x\", \"y\"] suffix")
    assert parsed == ["x", "y"]


def test_parse_terms_falls_back_to_lines():
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    parsed = pipeline._parse_terms("one\n\"two\"\n[three]")
    assert parsed == ["one", "two", "three"]


def test_generate_returns_cached_terms_without_llm_call():
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    pipeline.config = SimpleNamespace(model="dummy")
    pipeline.llm = SimpleNamespace(
        call_llm=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("should not call"))
    )
    pipeline._get_cached_terms = lambda post_id: ["cached-1", "cached-2"]
    pipeline._cache_terms = lambda post_id, terms: None

    terms = pipeline.generate(post_id="p1", post_title="t", post_text="txt")
    assert terms == ["cached-1", "cached-2"]


def test_generate_parses_and_caches_llm_response():
    cached = []
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    pipeline.config = SimpleNamespace(model="dummy")
    pipeline.llm = SimpleNamespace(call_llm=lambda **kwargs: "[\"term-a\", \"term-b\"]")
    pipeline._get_cached_terms = lambda post_id: None
    pipeline._cache_terms = lambda post_id, terms: cached.append((post_id, terms))

    terms = pipeline.generate(post_id="p2", post_title="title", post_url="https://x")

    assert terms == ["term-a", "term-b"]
    assert cached == [("p2", ["term-a", "term-b"])]


def test_generate_returns_empty_list_on_llm_error():
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    pipeline.config = SimpleNamespace(model="dummy")
    pipeline.llm = SimpleNamespace(
        call_llm=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("llm down"))
    )
    pipeline._get_cached_terms = lambda post_id: None
    pipeline._cache_terms = lambda post_id, terms: None

    assert pipeline.generate(post_id="p3") == []


def test_preview_generation_includes_retry_metadata_on_llm_error():
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    pipeline.config = SimpleNamespace(model="dummy")
    pipeline.llm = SimpleNamespace(
        call_llm=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("llm down")),
        last_call_metadata={
            "retry_count": 2,
            "http_status": 503,
            "response_snippet": "Service Unavailable",
            "elapsed_ms": 1234,
        },
    )
    pipeline._get_cached_terms = lambda post_id: None
    pipeline._cache_terms = lambda post_id, terms: None

    report = pipeline.preview_generation(post_id="p4", post_title="title")

    assert report["terms"] == []
    assert report["error_kind"] == "RuntimeError"
    assert report["retry_count"] == 2
    assert report["http_status"] == 503
    assert report["response_snippet"] == "Service Unavailable"
    assert report["cache_hit"] is False
    assert report["cache_error"] is None


def test_preview_generation_carries_cache_error_metadata():
    pipeline = GenSearchTermsPipeline.__new__(GenSearchTermsPipeline)
    pipeline.config = SimpleNamespace(model="dummy")
    pipeline.llm = SimpleNamespace(call_llm=lambda **kwargs: "[\"term-a\"]", last_call_metadata={})
    pipeline._get_cached_terms = lambda post_id: None
    pipeline._cache_terms = lambda post_id, terms: None
    pipeline._last_cache_error = "cache root must be array"

    report = pipeline.preview_generation(post_id="p5", post_title="title")

    assert report["terms"] == ["term-a"]
    assert report["cache_error"] == "cache root must be array"
    assert report["cache_hit"] is False
    assert report["parse_mode"] == "json_array"
