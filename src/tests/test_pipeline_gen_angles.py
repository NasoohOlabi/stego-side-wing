from types import SimpleNamespace

from workflows.pipelines.gen_angles import GenAnglesPipeline


def test_flatten_comments_handles_nested_replies():
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    comments = [
        {
            "id": "1",
            "body": "a",
            "replies": [{"id": "2", "body": "b", "replies": [{"id": "3", "body": "c"}]}],
        }
    ]

    flat = pipeline._flatten_comments(comments)
    assert [c["id"] for c in flat] == ["1", "2", "3"]


def test_build_dictionary_collects_post_search_and_comments():
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    post = {
        "selftext": "main body",
        "search_results": ["string result", {"snippet": "snippet text"}, {"text": "text field"}],
        "comments": [{"body": "comment body"}],
    }

    dictionary = pipeline._build_dictionary(post)
    assert "main body" in dictionary
    assert "string result" in dictionary
    assert "snippet text" in dictionary
    assert "text field" in dictionary
    assert "comment body" in dictionary


def test_generate_angles_filters_incomplete_results():
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts: {
            "results": [
                {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                {"source_quote": "q2", "tangent": "", "category": "c2"},
            ]
        }
    )

    post = {"selftext": "content"}
    angles = pipeline.generate_angles(post)
    assert angles == [{"source_quote": "q1", "tangent": "t1", "category": "c1"}]


def test_generate_angles_falls_back_to_llm():
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts: (_ for _ in ()).throw(RuntimeError("api down"))
    )
    pipeline._generate_angles_llm = lambda texts: [{"source_quote": "q", "tangent": "t", "category": "c"}]

    angles = pipeline.generate_angles({"selftext": "content"})
    assert angles == [{"source_quote": "q", "tangent": "t", "category": "c"}]


def test_process_posts_reads_processes_and_saves():
    saved = []
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": ["p1.json"]},
        get_post_local=lambda file_name, step: {"id": "p1"},
        save_post_local=lambda post, step: saved.append((post, step)),
    )
    pipeline.process_post = lambda post, step: {**post, "angles": [{"x": 1}], "options_count": 1}

    result = pipeline.process_posts(step="angles-step", count=1, offset=0)
    assert result[0]["options_count"] == 1
    assert saved[0][1] == "angles-step"
