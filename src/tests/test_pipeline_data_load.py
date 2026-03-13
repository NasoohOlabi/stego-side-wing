from types import SimpleNamespace

from workflows.contracts import FetchUrlResult
from workflows.pipelines.data_load import DataLoadPipeline


def test_process_posts_returns_empty_when_no_files():
    pipeline = DataLoadPipeline.__new__(DataLoadPipeline)
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": []}
    )
    pipeline.fetch_pipeline = SimpleNamespace(fetch=lambda url, use_cache: None)

    assert pipeline.process_posts() == []


def test_process_posts_fetches_and_saves_valid_posts_only():
    saves = []
    posts = {
        "a.json": {"id": "a", "url": "https://a"},
        "b.json": {"id": "b"},  # missing URL
        "c.json": {"id": "c", "url": "https://c"},  # empty fetched text
        "d.json": {"id": "d", "url": "https://d"},  # fetch failure
    }

    def fetch(url, use_cache):
        if url.endswith("/a"):
            return FetchUrlResult(url=url, success=True, text="filled")
        if url.endswith("/c"):
            return FetchUrlResult(url=url, success=True, text="   ")
        return FetchUrlResult(url=url, success=False, error="nope")

    pipeline = DataLoadPipeline.__new__(DataLoadPipeline)
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": list(posts.keys())},
        get_post_local=lambda file_name, step: dict(posts[file_name]),
        save_post_local=lambda post, step: saves.append((post, step)),
    )
    pipeline.fetch_pipeline = SimpleNamespace(fetch=fetch)

    result = pipeline.process_posts(batch_size=2, count=10, offset=0)

    assert len(result) == 1
    assert result[0]["id"] == "a"
    assert result[0]["selftext"] == "filled"
    assert saves == [(result[0], "filter-url-unresolved")]


def test_process_posts_continues_when_read_or_save_fails():
    saves = []
    pipeline = DataLoadPipeline.__new__(DataLoadPipeline)
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": ["bad.json", "ok.json"]},
        get_post_local=lambda file_name, step: (
            (_ for _ in ()).throw(RuntimeError("read fail"))
            if file_name == "bad.json"
            else {"id": "ok", "url": "https://ok"}
        ),
        save_post_local=lambda post, step: saves.append(post),
    )
    pipeline.fetch_pipeline = SimpleNamespace(
        fetch=lambda url, use_cache: FetchUrlResult(url=url, success=True, text="data")
    )

    result = pipeline.process_posts()
    assert [p["id"] for p in result] == ["ok"]
    assert len(saves) == 1
