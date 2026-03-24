from types import SimpleNamespace

import pytest

from workflows.contracts import FetchUrlResult
from workflows.pipelines.research import ResearchPipeline


@pytest.mark.parametrize(
    "post,expected",
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
    pipeline = ResearchPipeline.__new__(ResearchPipeline)
    with pytest.raises(ValueError, match="must have 'id' field"):
        pipeline.research_post({})


def test_research_post_skips_when_already_researched():
    post = {"id": "p1", "search_results": ["exists"]}
    pipeline = ResearchPipeline.__new__(ResearchPipeline)
    pipeline.gen_terms = SimpleNamespace(generate=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("should not run")))
    pipeline.backend = SimpleNamespace()
    pipeline.fetch_content = SimpleNamespace()

    assert pipeline.research_post(post) is post


def test_research_post_builds_deduped_non_pdf_results():
    pipeline = ResearchPipeline.__new__(ResearchPipeline)
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
        fetch=lambda url, use_cache: FetchUrlResult(
            url=url, success=True, text=f"text:{url}"
        )
    )

    post = {"id": "p1", "title": "t", "selftext": "body", "url": "https://origin"}
    result = pipeline.research_post(post)

    assert result["search_results"] == [
        "text:https://a.com/page",
        "text:https://b.com/page",
    ]


def test_process_posts_saves_local_for_all_and_remote_for_new_only():
    local_saves = []
    remote_saves = []
    posts = {
        "new.json": {"id": "new", "search_results": []},
        "old.json": {"id": "old", "search_results": ["existing"]},
    }

    pipeline = ResearchPipeline.__new__(ResearchPipeline)
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": ["new.json", "old.json"]},
        get_post_local=lambda file_name, step: dict(posts[file_name]),
        save_post_local=lambda post, step: local_saves.append(post["id"]),
        save_post=lambda post, step: remote_saves.append(post["id"]),
    )
    pipeline.research_post = lambda post, step, **kwargs: {**post, "processed": True}

    result = pipeline.process_posts(step="filter-researched", count=2, offset=0)

    assert [p["id"] for p in result] == ["new", "old"]
    assert local_saves == ["new", "old"]
    assert remote_saves == ["new"]
