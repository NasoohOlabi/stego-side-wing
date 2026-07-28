from types import SimpleNamespace

from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.utils.stego_codec import selection_channel_capacity_report
from workflows.utils.text_utils import build_post_text_dictionary_bundle


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
        analyze_angles=lambda texts, **_kwargs: {
            "results": [
                {"source_quote": "q1", "tangent": "t1", "category": "c1", "source_document": 0},
                {"source_quote": "q2", "tangent": "", "category": "c2"},
            ]
        }
    )

    post = {"selftext": "content"}
    angles = pipeline.generate_angles(post)
    assert angles == [
        {"source_quote": "q1", "tangent": "t1", "category": "c1", "source_document": 0},
    ]


def test_generate_angles_falls_back_to_llm():
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts, **_kwargs: (_ for _ in ()).throw(RuntimeError("api down"))
    )
    pipeline._generate_angles_llm = lambda texts: [
        {"source_quote": "q", "tangent": "t", "category": "c"}
    ]

    angles = pipeline.generate_angles({"selftext": "content"}, allow_fallback=True)
    assert angles == [
        {"source_quote": "q", "tangent": "t", "category": "c", "source_document": 0},
    ]


def test_process_posts_reads_processes_and_saves():
    saved = []
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": ["p1.json"]},
        get_post_local=lambda file_name, step: {"id": "p1"},
        save_post_local=lambda post, step: saved.append((post, step)),
    )
    pipeline.process_post = lambda post, step, allow_fallback=False: {
        **post,
        "angles": [{"x": 1}],
        "options_count": 1,
    }

    result = pipeline.process_posts(step="angles-step", count=1, offset=0)
    assert result[0]["options_count"] == 1
    assert saved[0][1] == "angles-step"


def test_process_posts_records_partial_failure_summary():
    saved = []
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)

    def _get_post_local(file_name, step):
        if file_name == "p1.json":
            return {"id": "p1"}
        raise RuntimeError("missing post")

    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset: {"fileNames": ["p1.json", "p2.json"]},
        get_post_local=_get_post_local,
        save_post_local=lambda post, step: saved.append((post, step)),
    )
    pipeline.process_post = lambda post, step, allow_fallback=False: {
        **post,
        "angles": [{"x": 1}],
        "options_count": 1,
    }

    result = pipeline.process_posts(step="angles-step", count=2, offset=0)

    assert len(result) == 1
    assert pipeline._last_batch_summary["requested_count"] == 2
    assert pipeline._last_batch_summary["loaded_count"] == 1
    assert pipeline._last_batch_summary["load_failed_count"] == 1
    assert pipeline._last_batch_summary["processed_count"] == 1
    assert pipeline._last_batch_summary["failed_count"] == 1


def test_build_post_text_dictionary_bundle_caps_sources_deterministically(
    monkeypatch, clear_workflow_capacity_env
):
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS", "1")
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_COMMENTS", "2")
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", "3")
    post = {
        "selftext": "body",
        "search_results": ["s1", "s2"],
        "comments": [
            {"id": "c1", "body": "c1"},
            {"id": "c2", "body": "c2"},
            {"id": "c3", "body": "c3"},
        ],
    }

    first = build_post_text_dictionary_bundle(post, apply_capacity_profile=True)
    second = build_post_text_dictionary_bundle(post, apply_capacity_profile=True)

    assert first["texts"][0] == "body"
    assert len(first["texts"]) == 3
    assert sum(text.startswith("s") for text in first["texts"]) == 1
    assert sum(text.startswith("c") for text in first["texts"]) == 1
    assert first["report"]["dictionary_id"] == second["report"]["dictionary_id"]
    assert first["report"]["raw_source_counts"] == {
        "post": 1,
        "search_results": 2,
        "comments": 3,
    }
    assert first["report"]["source_counts"] == {
        "post": 1,
        "search_results": 1,
        "comments": 1,
    }
    assert set(first["report"]["truncated_sources"]) == {
        "search_results",
        "comments",
        "total",
    }


def test_balanced_dictionary_caps_pathological_thread(
    clear_workflow_capacity_env,
) -> None:
    post = {
        "selftext": "thread body",
        "search_results": [f"research {index}" for index in range(100)],
        "comments": [
            {"id": f"c{index}", "body": f"short relevant comment {index}"}
            for index in range(12_704)
        ],
    }

    result = build_post_text_dictionary_bundle(post, apply_capacity_profile=True)

    assert len(result["texts"]) == 64
    assert result["report"]["source_counts"] == {
        "post": 1,
        "search_results": 16,
        "comments": 47,
    }
    assert result["report"]["sampler_version"] == "stable_round_robin_v1"


def test_preview_post_reports_dictionary_capacity(monkeypatch, clear_workflow_capacity_env):
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS", "1")
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_COMMENTS", "1")
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", "2")
    captured = {}

    def _analyze_angles(texts, **kwargs):
        captured["texts"] = list(texts)
        captured["max_results"] = kwargs.get("max_results")
        return {
            "results": [
                {
                    "source_quote": "q1",
                    "tangent": "t1",
                    "category": "c1",
                    "source_document": 0,
                }
            ]
        }

    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(analyze_angles=_analyze_angles)

    result = pipeline.preview_post(
        {
            "id": "post-1",
            "selftext": "body",
            "search_results": ["s1", "s2"],
            "comments": [{"id": "c1", "body": "c1"}],
        }
    )

    assert captured["texts"][0] == "body"
    assert len(captured["texts"]) == 2
    assert captured["max_results"] == 128
    assert result["report"]["input_capacity_applied"] is True
    assert result["report"]["input_raw_count"] == 4
    assert result["report"]["input_source_counts"] == {
        "post": 1,
        "search_results": 1,
        "comments": 0,
    }
    assert set(result["report"]["input_truncated_sources"]) == {
        "search_results",
        "total",
    }


def test_preview_post_caps_angles_output(monkeypatch, clear_workflow_capacity_env):
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "1")
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts, **_kwargs: {
            "results": [
                {"source_quote": "q1", "tangent": "t1", "category": "c1", "source_document": 0},
                {"source_quote": "q2", "tangent": "t2", "category": "c2", "source_document": 0},
            ]
        }
    )

    out = pipeline.preview_post({"id": "p-a", "selftext": "body"})
    assert len(out["report"]["angles"]) == 1
    assert out["report"]["angles_raw_count"] == 2
    assert out["report"]["angles_capped"] is True
    assert out["report"]["angles_max_output"] == 1


def test_preview_post_persists_versioned_artifact_metadata(
    monkeypatch, clear_workflow_capacity_env
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "2")
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts, **_kwargs: {
            "results": [
                {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                {"source_quote": "q2", "tangent": "t2", "category": "c2"},
            ]
        }
    )

    out = pipeline.preview_post({"id": "p-versioned", "selftext": "body"})

    assert out["report"]["angles_raw_count"] == 3
    assert out["report"]["options_count"] == 2
    assert out["report"]["angles_target_reached"] is True
    assert out["report"]["angles_target_shortfall"] == 0
    assert out["post"]["angle_artifact"] == {
        "schema_version": 2,
        "artifact_namespace": "selection_channel_angles/refactor_v2",
        "generator_version": "efficient_multiframe_selection_v1",
        "sampler_version": "stable_round_robin_v1",
        "selection_strategy": "stable_source_rank_round_robin",
        "dictionary_id": out["report"]["dictionary_id"],
        "capacity_profile": "mid",
        "capacity_limits": out["report"]["input_capacity_limits"],
        "generation_mode": "model",
        "angles_retained_target": 2,
        "angles_raw_target": 8,
        "raw_target_multiplier": 4,
        "angles_target_reached": True,
        "angles_target_shortfall": 0,
    }


def test_preview_post_reports_shortfall_after_deduplication(
    monkeypatch, clear_workflow_capacity_env
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "2")
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts, **_kwargs: {
            "results": [
                {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                {"source_quote": "q1", "tangent": "t1", "category": "c1"},
            ]
        }
    )

    out = pipeline.preview_post({"id": "p-shortfall", "selftext": "body"})

    assert out["report"]["options_count"] == 1
    assert out["report"]["angles_target_reached"] is False
    assert out["report"]["angles_target_shortfall"] == 1
    assert out["post"]["angle_artifact"]["angles_target_shortfall"] == 1


def test_preview_post_unbounded_mode_has_no_target_metrics(
    monkeypatch, clear_workflow_capacity_env
) -> None:
    monkeypatch.setenv("WORKFLOW_CAPACITY_LIMITS_ENABLED", "0")
    captured = {}
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts, **kwargs: captured.update(kwargs) or {"results": []}
    )

    out = pipeline.preview_post({"id": "p-unbounded", "selftext": "body"})

    assert captured["max_results"] is None
    assert out["report"]["angles_retained_target"] is None
    assert out["report"]["angles_raw_target"] is None
    assert out["report"]["angles_target_reached"] is None
    assert out["report"]["angles_target_shortfall"] is None
    assert out["post"]["angle_artifact"]["angles_target_shortfall"] is None


def test_tangent_db_builder_receives_raw_target(
    monkeypatch, clear_workflow_capacity_env
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "2")
    monkeypatch.setenv("WORKFLOW_ANGLES_RAW_TARGET_MULTIPLIER", "3")
    captured = {}

    def _builder(**kwargs):
        captured["max_output"] = kwargs["max_output"]
        return kwargs["angles"]

    monkeypatch.setattr(
        "workflows.pipelines.gen_angles._apply_tangent_db_builder",
        _builder,
    )
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts, **kwargs: {
            "results": [
                {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                {"source_quote": "q2", "tangent": "t2", "category": "c2"},
                {"source_quote": "q3", "tangent": "t3", "category": "c3"},
            ]
        }
    )

    out = pipeline.preview_post({"id": "p-raw-builder", "selftext": "body"})

    assert captured["max_output"] == 6
    assert out["report"]["options_count"] == 2


def test_angle_sampling_does_not_change_comment_choice_count(
    monkeypatch, clear_workflow_capacity_env
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", "2")
    post = {
        "id": "p-parent-capacity",
        "selftext": "body",
        "comments": [
            {"id": f"c{index}", "body": f"comment {index}"}
            for index in range(20)
        ],
    }
    expected = selection_channel_capacity_report(post)["comment_choices"]
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda texts, **kwargs: {"results": []}
    )

    sampled = pipeline.preview_post(post)["post"]

    assert selection_channel_capacity_report(sampled)["comment_choices"] == expected
    assert expected == 21


def test_preview_post_extractive_zero_kld_mode_skips_backend(
    monkeypatch, clear_workflow_capacity_env
):
    monkeypatch.setenv("WORKFLOW_ANGLES_GENERATION_MODE", "extractive_zero_kld")
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        analyze_angles=lambda _texts, **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend not expected")
        )
    )

    out = pipeline.preview_post(
        {
            "id": "p-z",
            "selftext": "A practical trick is to keep the wording close to the source material.",
            "comments": [
                {
                    "id": "c1",
                    "body": "This wording already sounds like a normal comment and should stay in distribution.",
                }
            ],
        }
    )

    assert out["report"]["generation_mode"] == "extractive_zero_kld"
    assert out["report"]["options_count"] >= 1
    assert out["report"]["angles"][0]["source_quote"]
    assert out["report"]["angles"][0]["tangent"] == out["report"]["angles"][0]["source_quote"]
    assert out["post"]["options_count"] == len(out["post"]["angles"])


def test_tangent_db_v1_emits_selected_angles_and_persists_report(
    monkeypatch, clear_workflow_capacity_env
):
    monkeypatch.setenv("WORKFLOW_ANGLES_GENERATION_MODE", "extractive_zero_kld")
    monkeypatch.setenv("WORKFLOW_TANGENT_DB_BUILDER", "v1")
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace()
    post = {
        "id": "p-shadow",
        "title": "Flash flood leaves campers missing",
        "selftext": "Rescue teams searched the river through the night.",
        "search_results": [
            "Competitive dynamics among coffee retailers pressured quarterly margins."
        ],
    }

    out = pipeline.preview_post(post)

    assert len(out["post"]["angles"]) == 1
    assert "Rescue teams" in out["post"]["angles"][0]["tangent"]
    tangent_report = out["post"]["tangent_db_report"]
    assert tangent_report == out["report"]["tangent_db_report"]
    assert tangent_report["input_candidate_count"] == 2
    assert tangent_report["kept_count"] == 1
    assert tangent_report["dropped"]["low_thread_relevance"] == 1
    assert out["report"]["angles"] == out["post"]["angles"]


def test_tangent_db_v1_selection_is_sender_receiver_reproducible(
    monkeypatch, clear_workflow_capacity_env
):
    monkeypatch.setenv("WORKFLOW_ANGLES_GENERATION_MODE", "extractive_zero_kld")
    monkeypatch.setenv("WORKFLOW_TANGENT_DB_BUILDER", "v1")
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace()
    post = {
        "id": "p-parity",
        "title": "Flash flood rescue",
        "selftext": "Rescue teams searched the flooded river through the night.",
        "comments": [{"body": "Campers waited while rescue crews searched downstream."}],
        "search_results": ["Coffee retailers reported lower quarterly margins."],
    }

    sender = pipeline.preview_post(post)["post"]
    receiver = pipeline.preview_post(post)["post"]

    assert receiver["angles"] == sender["angles"]
    assert (
        receiver["tangent_db_report"]["config_hash"] == sender["tangent_db_report"]["config_hash"]
    )


def test_tangent_db_legacy_does_not_add_report(monkeypatch, clear_workflow_capacity_env):
    monkeypatch.setenv("WORKFLOW_ANGLES_GENERATION_MODE", "extractive_zero_kld")
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace()

    out = pipeline.preview_post({"id": "p-legacy", "selftext": "A long enough source sentence."})

    assert "tangent_db_report" not in out["post"]
    assert "tangent_db_report" not in out["report"]


def test_process_posts_uses_tagged_queue_and_saves_tagged_filename():
    saved = []
    seen = {}
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        posts_list=lambda step, count, offset, tag=None: (
            seen.update({"step": step, "count": count, "offset": offset, "tag": tag})
            or {"fileNames": ["p1.json"]}
        ),
        get_post_local=lambda file_name, step: {"id": "p1"},
        save_object_local=lambda data, step, filename: saved.append((step, filename)),
    )
    pipeline.process_post = lambda post, step, allow_fallback=False: {
        **post,
        "angles": [{"x": 1}],
        "options_count": 1,
    }

    result = pipeline.process_posts(step="angles-step", count=1, offset=0, tag="exp")

    assert result[0]["options_count"] == 1
    assert seen == {"step": "angles-step", "count": 1, "offset": 0, "tag": "exp"}
    assert saved == [("angles-step", "p1_exp.json")]


def test_process_post_id_prefers_tagged_source_when_present():
    calls = []
    pipeline = GenAnglesPipeline.__new__(GenAnglesPipeline)
    pipeline.backend = SimpleNamespace(
        get_post_local=lambda file_name, step: calls.append((file_name, step)) or {"id": "p1"},
        save_object_local=lambda data, step, filename: None,
    )
    pipeline.process_post = lambda post, step, allow_fallback=False: {
        **post,
        "angles": [{"x": 1}],
        "options_count": 1,
    }

    pipeline.process_post_id("p1", step="angles-step", tag="exp")

    assert calls[0] == ("p1_exp.json", "angles-step")
