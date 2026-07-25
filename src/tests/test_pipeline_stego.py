from types import SimpleNamespace

import pytest

from workflows.pipelines import stego
from workflows.pipelines.stego import StegoPipeline
from workflows.utils.output_results_shape import n8n_save_object_body
from workflows.utils.stego_codec import extract_invisible_payload

FORBIDDEN_INVISIBLE_CHARS = {"\u200c", "\u200d", "\u2060", "\u2063"}


def assert_no_invisible_carrier(text: str) -> None:
    assert not (set(text) & FORBIDDEN_INVISIBLE_CHARS)


def test_n8n_save_object_body_legacy_shape():
    body = n8n_save_object_body({"stego_text": "x", "embedding": {"a": 1}, "post": {"id": "1"}})
    assert body[0]["stegoText"] == "x"
    assert body[0]["embedding"]["a"] == 1
    assert "generationMeta" in body[0]["embedding"]
    assert set(body[0]) == {"stegoText", "embedding", "post"}
    assert n8n_save_object_body({})[0]["embedding"]["generationMeta"]["schemaVersion"] == 1


def test_stego_helpers_cover_edge_cases():
    assert stego._is_non_empty_string("x") is True
    assert stego._is_non_empty_string("") is False
    assert stego._get_bit_width(0) == 0
    assert stego._get_bit_width(8) >= 1
    taken, remaining, insufficient = stego._take_bits("101", 5)
    assert taken == "10100"
    assert remaining == ""
    assert insufficient is True


def test_stego_comment_strings_from_parsed_requires_three_strings() -> None:
    assert stego._stego_comment_strings_from_parsed(["a", "b", "c"]) == ["a", "b", "c"]
    assert stego._stego_comment_strings_from_parsed(["a", "b"]) is None
    assert stego._stego_comment_strings_from_parsed(["a", "b", "c", "d"]) is None
    assert stego._stego_comment_strings_from_parsed({"texts": ["x", "y", "z"]}) == ["x", "y", "z"]
    assert stego._stego_comment_strings_from_parsed({"texts": ["x", "y"]}) is None


def test_stego_flatten_and_eq_helpers():
    flat = stego._flatten_comments(
        [{"id": "a", "replies": [{"id": "b", "replies": [{"id": "c"}]}]}]
    )
    assert [x["id"] for x in flat] == ["a", "b", "c"]
    assert stego._eq_angle(
        {"category": "c", "tangent": "t", "source_quote": "q"},
        {"category": "c", "tangent": "t", "source_quote": "q"},
    )
    assert not stego._eq_angle({"category": "a"}, {"category": "b"})


def test_compress_payload_can_use_standard_encoding():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    result = pipeline._compress_payload(payload="abc", dictionary=[])
    assert result["method"] == "standard"
    assert result["compressed"].startswith("0")


def test_encode_requires_angles():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    with pytest.raises(ValueError, match="Post must have angles"):
        pipeline.encode(payload="secret", post={"id": "1", "angles": []})


def test_encode_returns_success_with_mocked_stages():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    pipeline._augment_post = lambda payload, post: {
        "commentEmbedding": {"context": {"title": "t", "author": "a"}},
        "angleEmbedding": {
            "selectedAngle": {"idx": 2, "category": "c", "tangent": "t", "source_quote": "q"},
            "totalAnglesSelectedFirst": [],
            "TangentsDB": [],
        },
    }
    pipeline._build_samples = lambda aug, post: (
        [{"category": "c", "source_quote": "q", "tangent": "t"}],
        [{"category": "c", "source_quote": "q", "tangent": "t"}],
    )
    pipeline._generate_stego_texts = lambda sample, comment_embedding, **kwargs: ["candidate text"]
    pipeline._evaluate_candidate_groups = lambda **kwargs: {
        "succeeded": True,
        "accepted_candidate": {
            "text": "candidate text",
            "group_index": 0,
            "candidate_index": 0,
            "decoded_index": 2,
            "strict_decoded_index": 2,
        },
        "validationDetails": {"candidates": [{"decoded_index": 2}]},
    }

    post = {"id": "p1", "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}]}
    result = pipeline.encode(payload="secret", post=post, tag="tag")

    assert result["succeeded"] is True
    assert result["stego_text"] == "candidate text"
    assert extract_invisible_payload(result["stego_text"]) is None
    assert_no_invisible_carrier(result["stego_text"])
    assert result["sender_audit"]["payload_carrier"] == "selection_channel"
    assert result["angle_index"] == 2


def test_encode_binary_selection_bits_returns_success_with_mocked_stages():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    angle = {"idx": 1, "category": "c", "tangent": "t", "source_quote": "q"}
    pipeline._build_samples = lambda aug, post: ([angle], [angle])
    pipeline._generate_stego_texts = lambda **kwargs: ["candidate text"]
    pipeline._evaluate_candidate_groups = lambda **kwargs: {
        "succeeded": True,
        "accepted_candidate": {
            "text": "candidate text",
            "group_index": 0,
            "candidate_index": 0,
            "decoded_index": 1,
            "strict_decoded_index": 1,
        },
        "validationDetails": {"candidates": [{"decoded_index": 1}]},
    }
    post = {
        "id": "p-bits",
        "comments": [],
        "angles": [
            {"source_quote": "q0", "tangent": "t0", "category": "c0"},
            {"source_quote": "q", "tangent": "t", "category": "c"},
        ],
    }

    result = pipeline.encode_binary_selection_bits(bits="1", post=post, tag="scan", max_retries=0)

    assert result["succeeded"] is True
    assert result["stego_text"] == "candidate text"
    assert result["angle_index"] == 1
    assert result["binary_selection_bits"] == "1"
    assert result["compression_skipped"] is True
    assert result["payload_transform_skipped"] is True
    assert result["sender_audit"]["payload_transform"] == "diagnostic_binary_selection_bits"


def test_encode_binary_selection_bits_reports_json_failure():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    angle = {"idx": 0, "category": "c", "tangent": "t", "source_quote": "q"}
    pipeline._build_samples = lambda aug, post: ([angle], [angle])

    def fail_generation(**kwargs):
        raise RuntimeError("Stego LLM output must be valid JSON")

    pipeline._generate_stego_texts = fail_generation
    post = {"id": "p-bits", "comments": [], "angles": [angle]}

    result = pipeline.encode_binary_selection_bits(bits="0", post=post, max_retries=0)

    assert result["succeeded"] is False
    assert "valid JSON" in result["error"]
    assert result["compression_skipped"] is True


def test_encode_binary_selection_bits_reports_decode_mismatch():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    selected = {"idx": 0, "category": "c", "tangent": "t", "source_quote": "q"}
    decoded = {"idx": 1, "category": "other", "tangent": "other", "source_quote": "other"}
    pipeline._build_samples = lambda aug, post: ([selected], [selected, decoded])
    pipeline._generate_stego_texts = lambda **kwargs: ["drifted text"]
    pipeline._evaluate_candidate_groups = lambda **kwargs: {
        "succeeded": False,
        "validationDetails": {
            "candidates": [
                {
                    "decoded_index": 1,
                    "decoded_angle": decoded,
                    "rejection_reasons": ["decode_mismatch"],
                }
            ]
        },
    }
    post = {"id": "p-bits", "comments": [], "angles": [selected, decoded]}

    result = pipeline.encode_binary_selection_bits(bits="00", post=post, max_retries=0)

    assert result["succeeded"] is False
    assert result["error"] == "Decoding validation failed"
    assert result["validation_details"]["candidates"][0]["decoded_index"] == 1


def test_plan_payload_frames_is_deterministic_and_skips_zero_capacity_posts():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    posts = [{"id": "p0", "comments": [], "angles": []}]
    for idx in range(1, 40):
        posts.append(
            {
                "id": f"p{idx}",
                "comments": [
                    {"id": f"c{idx}a", "author": "a", "body": "one", "replies": []},
                    {"id": f"c{idx}b", "author": "b", "body": "two", "replies": []},
                ],
                "angles": [
                    {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                    {"source_quote": "q2", "tangent": "t2", "category": "c2"},
                    {"source_quote": "q3", "tangent": "t3", "category": "c3"},
                ],
            }
        )

    plan1 = pipeline.plan_payload_frames("hi", posts, max_frames_per_post=2)
    plan2 = pipeline.plan_payload_frames("hi", posts, max_frames_per_post=2)

    assert plan1["succeeded"] is True
    assert plan1["frames"] == plan2["frames"]
    assert all(frame["post_id"] != "p0" for frame in plan1["frames"])
    assert all(frame["capacity_report"]["comment_choices"] == 3 for frame in plan1["frames"])
    assert all(frame["capacity_report"]["tangent_choices"] == 3 for frame in plan1["frames"])
    assert (
        sum(frame["bits_used"] for frame in plan1["frames"])
        == plan1["recovery_meta"]["stream_bit_length"]
    )
    assert all(frame["padding_bits"] == 0 for frame in plan1["frames"][:-1])


def test_encode_payload_frames_builds_local_artifact_feed():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    pipeline.encode_binary_selection_bits = lambda bits, post, tag=None, max_retries=0: {
        "succeeded": True,
        "stego_text": f"carrier-{bits}",
        "angle_index": 0,
        "sender_audit": {"bits": bits},
    }
    posts = []
    for idx in range(40):
        posts.append(
            {
                "id": f"p{idx + 1}",
                "comments": [
                    {"id": f"c{idx + 1}a", "author": "a", "body": "one", "replies": []},
                    {"id": f"c{idx + 1}b", "author": "b", "body": "two", "replies": []},
                ],
                "angles": [
                    {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                    {"source_quote": "q2", "tangent": "t2", "category": "c2"},
                    {"source_quote": "q3", "tangent": "t3", "category": "c3"},
                ],
            }
        )

    out = pipeline.encode_payload_frames("hi", posts, max_frames_per_post=3, tag="t")

    assert out["succeeded"] is True
    assert out["frames"]
    assert out["posts"]
    assert out["ordered_frame_refs"]
    assert any(
        comment["author"] == "sender"
        for post in out["posts"]
        for comment in stego._flatten_comments(post.get("comments", []))
    )


def test_encode_uses_context_sharpen_after_validation_exhausted():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    angle = {"idx": 2, "category": "c", "tangent": "target tangent", "source_quote": "q"}
    pipeline._augment_post = lambda payload, post: {
        "commentEmbedding": {"context": {"title": "t", "author": "a"}},
        "angleEmbedding": {
            "selectedAngle": angle,
            "totalAnglesSelectedFirst": [],
            "TangentsDB": [angle],
        },
    }
    pipeline._build_samples = lambda aug, post: ([angle], [angle])
    pipeline._generate_stego_texts = lambda **kwargs: ["drifted candidate"]
    validations = [
        {
            "succeeded": False,
            "promising_candidates": [
                {
                    "text": "drifted candidate",
                    "group_index": 0,
                    "candidate_index": 0,
                    "decoded_index": 4,
                    "strict_decoded_index": None,
                    "context_gate": {"passes": True},
                    "distance_bucket": "adjacent",
                    "matches_selected_angle": False,
                }
            ],
            "promising_candidate": {
                "text": "drifted candidate",
                "group_index": 0,
                "candidate_index": 0,
                "decoded_index": 4,
                "strict_decoded_index": None,
                "context_gate": {"passes": True},
                "distance_bucket": "adjacent",
            },
            "validationDetails": {"candidates": [{"decoded_index": 4}]},
        },
        {
            "succeeded": True,
            "accepted_candidate": {
                "text": "revised target tangent reply",
                "group_index": 0,
                "candidate_index": 0,
                "decoded_index": 2,
                "strict_decoded_index": 2,
            },
            "validationDetails": {"candidates": [{"decoded_index": 2}]},
        },
    ]
    pipeline._evaluate_candidate_groups = lambda **kwargs: validations.pop(0)
    pipeline._revise_candidate_text_contextually = lambda **kwargs: "revised target tangent reply"

    post = {"id": "p1", "angles": [angle]}
    result = pipeline.encode(payload="secret", post=post, tag="tag", max_retries=0)

    assert result["succeeded"] is True
    assert result["stego_text"] == "revised target tangent reply"
    assert_no_invisible_carrier(result["stego_text"])
    assert result["encoded_samples"][-1]["generation_mode"] == "context_sharpen"
    assert result["sender_audit"]["candidate_validation"]["acceptance_source"] == "context_sharpen"


def test_encode_context_sharpen_tries_multiple_promising_candidates():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    angle = {"idx": 2, "category": "c", "tangent": "target tangent", "source_quote": "q"}
    pipeline._augment_post = lambda payload, post: {
        "commentEmbedding": {"context": {"title": "t", "author": "a"}},
        "angleEmbedding": {
            "selectedAngle": angle,
            "totalAnglesSelectedFirst": [],
            "TangentsDB": [angle],
        },
    }
    pipeline._build_samples = lambda aug, post: ([angle], [angle])
    pipeline._generate_stego_texts = lambda **kwargs: ["drifted candidate"]
    validations = [
        {
            "succeeded": False,
            "promising_candidates": [
                {
                    "text": "first draft",
                    "group_index": 0,
                    "candidate_index": 0,
                    "decoded_index": 4,
                    "strict_decoded_index": None,
                    "context_gate": {"passes": False},
                    "distance_bucket": "adjacent",
                    "matches_selected_angle": False,
                },
                {
                    "text": "second draft",
                    "group_index": 0,
                    "candidate_index": 1,
                    "decoded_index": 2,
                    "strict_decoded_index": None,
                    "context_gate": {"passes": False},
                    "distance_bucket": "exact",
                    "matches_selected_angle": True,
                },
            ],
            "validationDetails": {"candidates": [{"decoded_index": 4}, {"decoded_index": 2}]},
        },
        {"succeeded": False, "accepted_candidate": None, "validationDetails": {"candidates": []}},
        {
            "succeeded": True,
            "accepted_candidate": {
                "text": "fixed second draft",
                "group_index": 0,
                "candidate_index": 0,
                "decoded_index": 2,
                "strict_decoded_index": 2,
            },
            "validationDetails": {"candidates": [{"decoded_index": 2}]},
        },
    ]
    pipeline._evaluate_candidate_groups = lambda **kwargs: validations.pop(0)
    sharpened_inputs: list[str] = []

    def fake_revise_candidate_text_contextually(**kwargs):
        sharpened_inputs.append(kwargs["candidate_text"])
        return "fixed second draft" if kwargs["candidate_text"] == "second draft" else "still wrong"

    pipeline._revise_candidate_text_contextually = fake_revise_candidate_text_contextually

    post = {"id": "p1", "angles": [angle]}
    result = pipeline.encode(payload="secret", post=post, tag="tag", max_retries=0)

    assert result["succeeded"] is True
    assert sharpened_inputs == ["first draft", "second draft"]
    assert result["stego_text"] == "fixed second draft"


def test_encode_returns_error_when_no_samples():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    pipeline._augment_post = lambda payload, post: {"angleEmbedding": {"selectedAngle": {"idx": 0}}}
    pipeline._build_samples = lambda aug, post: ([], [])

    post = {"id": "p1", "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}]}
    result = pipeline.encode(payload="secret", post=post)
    assert result["succeeded"] is False
    assert "No samples generated" in result["error"]


def test_process_post_falls_back_to_angles_step_and_persists_on_success():
    calls = []
    pipeline = StegoPipeline.__new__(StegoPipeline)
    pipeline.backend = SimpleNamespace(
        get_post_local=lambda filename, step: (
            (_ for _ in ()).throw(FileNotFoundError("missing"))
            if step == "final-step"
            else {"id": "p9", "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}]}
        ),
        save_object_local=lambda data, step, filename: calls.append(
            (step, filename, bool(data and data[0].get("stegoText")))
        ),
    )
    pipeline.encode = lambda payload, post, tag: {
        "succeeded": True,
        "post": post,
        "stego_text": "ok",
    }

    result = pipeline.process_post(post_id="p9", payload="x", tag="v1", step="final-step")

    assert result["succeeded"] is True
    assert calls == [("final-step", "p9_v1.json", True)]


def test_process_post_auto_selects_next_unprocessed_post_with_tag():
    calls = []
    selected = {}
    pipeline = StegoPipeline.__new__(StegoPipeline)

    def fake_posts_list(step, count, offset, tag):
        selected.update({"step": step, "count": count, "offset": offset, "tag": tag})
        return {"fileNames": ["p10.json"]}

    pipeline.backend = SimpleNamespace(
        posts_list=fake_posts_list,
        get_post_local=lambda filename, step: {
            "id": "p10",
            "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}],
        },
        save_object_local=lambda data, step, filename: calls.append((step, filename)),
    )
    pipeline.load_default_payload_and_tag = lambda: ("default payload", "same-tag")
    pipeline.encode = lambda payload, post, tag: {
        "succeeded": True,
        "post": post,
        "stego_text": "ok",
        "tag": tag,
    }

    result = pipeline.process_post()

    assert result["succeeded"] is True
    assert selected == {
        "step": "final-step",
        "count": 1,
        "offset": 1,
        "tag": "same-tag",
    }
    assert calls == [("final-step", "p10_same-tag.json")]


def test_process_post_falls_back_to_auto_select_when_post_id_missing_on_disk():
    saved = []
    selected = {}
    pipeline = StegoPipeline.__new__(StegoPipeline)

    def fake_posts_list(step, count, offset, tag):
        selected.update({"step": step, "count": count, "offset": offset, "tag": tag})
        return {"fileNames": ["p11.json"]}

    def fake_get_post_local(filename, step):
        if filename == "missing-post.json":
            raise FileNotFoundError("missing")
        return {
            "id": "p11",
            "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}],
        }

    pipeline.backend = SimpleNamespace(
        posts_list=fake_posts_list,
        get_post_local=fake_get_post_local,
        save_object_local=lambda data, step, filename: saved.append((step, filename)),
    )
    pipeline.load_default_payload_and_tag = lambda: ("default payload", "same-tag")
    pipeline.encode = lambda payload, post, tag: {
        "succeeded": True,
        "post": post,
        "stego_text": "ok",
        "tag": tag,
    }

    result = pipeline.process_post(post_id="missing-post")

    assert result["succeeded"] is True
    assert selected == {
        "step": "final-step",
        "count": 1,
        "offset": 1,
        "tag": "same-tag",
    }
    assert saved == [("final-step", "p11_same-tag.json")]


def test_contextuality_gate_rejects_generic_editorial_drift():
    post_augmentation = {
        "commentEmbedding": {
            "context": {
                "title": "Title",
                "selftext": "A local government story.",
            },
            "pickedCommentChain": [
                {"name": "u1", "body": "This looks politically motivated and cruel."}
            ],
        }
    }
    sample = {
        "source_quote": "This looks politically motivated and cruel.",
        "tangent": "Examine the political motivations behind the policy.",
        "category": "Politics",
        "best_match": "This looks politically motivated and cruel.",
    }
    result = stego._contextuality_gate(
        "At the end of the day, the bigger issue is a wake up call for all of society.",
        post_augmentation=post_augmentation,
        sample=sample,
        selected_angle=sample,
    )

    assert result["passes"] is False
    assert "generic_editorial_tone" in result["reasons"]


def test_selected_angle_anchor_variant_adds_distinctive_phrase():
    selected_angle = {
        "category": "Reference Material",
        "source_quote": '{"title": "Immigrant Defenders Law Center", "summary": "Provides free legal aid to underserved communities."}',
        "tangent": "Discuss legal defense resources.",
    }

    result = stego._with_selected_angle_anchor_variants(
        ["This is terrifying and someone needs to help her family."],
        selected_angle,
    )

    assert len(result) == 2
    assert "Immigrant Defenders Law Center" in result[1]


def test_evaluate_candidate_groups_prefers_context_safe_exact_match():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    selected_angle = {
        "idx": 1,
        "category": "Politics",
        "tangent": "policy abuse",
        "source_quote": "policy abuse",
    }
    tangents_db = [
        {"idx": 1, "category": "Other", "tangent": "other", "source_quote": "other"},
        selected_angle,
    ]
    post_augmentation = {
        "commentEmbedding": {
            "context": {"title": "Title", "selftext": "policy abuse"},
            "pickedCommentChain": [{"name": "u1", "body": "This feels like policy abuse."}],
        }
    }
    decode_calls = iter(
        [
            (None, 1),
            (1, 1),
            (1, 1),
        ]
    )
    pipeline._decode_candidate = lambda **kwargs: next(decode_calls)
    encoded_results = [
        {
            "category": "Other",
            "source_quote": "other",
            "tangent": "other",
            "prompt_style": "natural",
            "texts": ["At the end of the day, the bigger issue is society."],
        },
        {
            "category": "Politics",
            "source_quote": "policy abuse",
            "tangent": "policy abuse",
            "prompt_style": "natural",
            "texts": ["This really does feel like policy abuse dressed up as procedure."],
        },
    ]

    result = pipeline._evaluate_candidate_groups(
        encoded_results=encoded_results,
        tangents_db=tangents_db,
        selected_angle=selected_angle,
        post_augmentation=post_augmentation,
    )

    assert result["succeeded"] is True
    assert result["accepted_candidate"]["group_index"] == 1


def test_evaluate_candidate_groups_prefers_model_reply_over_synthetic_anchor():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    selected_angle = {
        "idx": 0,
        "category": "Work",
        "tangent": "ending the work day",
        "source_quote": "Putting the laptop away helps end the work day.",
    }
    post_augmentation = {
        "commentEmbedding": {
            "context": {"title": "Working from home", "selftext": "Need a routine."},
            "pickedCommentChain": [{"name": "u1", "body": "I put my laptop away at five."}],
        }
    }
    pipeline._decode_candidate = lambda **kwargs: (0, 1)
    result = pipeline._evaluate_candidate_groups(
        encoded_results=[
            {
                "category": "Work",
                "source_quote": selected_angle["source_quote"],
                "tangent": selected_angle["tangent"],
                "prompt_style": "natural",
                "texts": [
                    "Packing the laptop away at five has made the evening feel separate from work.",
                    "Putting the laptop away helps end the work day. I can see why people keep coming back to that point.",
                ],
            }
        ],
        tangents_db=[selected_angle],
        selected_angle=selected_angle,
        post_augmentation=post_augmentation,
    )

    assert result["succeeded"] is True
    assert result["accepted_candidate"]["candidate_index"] == 0
    assert result["validationDetails"]["candidates"][0]["is_synthetic_anchor"] is False


def test_decode_rerank_promotes_lexically_grounded_candidate():
    candidates = [
        {
            "index": 10,
            "score": 0.7,
            "angle": {
                "category": "Community Discussion",
                "source_quote": "I fear for their safety at this point.",
                "tangent": "I fear for their safety at this point.",
            },
        },
        {
            "index": 3,
            "score": 0.62,
            "angle": {
                "category": "Reference Material",
                "source_quote": "Immigrant Defense Project Helpline report ICE raid ruse",
                "tangent": "To report an ICE raid where ICE has used a ruse, call the Immigrant Defense Project Helpline.",
            },
        },
    ]

    from workflows.pipelines.decode import _rerank_decode_candidates

    result = _rerank_decode_candidates(
        stego_text="I hope someone calls the Immigrant Defense Project Helpline to report this fake ICE raid.",
        candidates=candidates,
        limit=2,
    )

    assert result[0]["index"] == 3


def test_process_post_skips_save_when_encode_failed():
    saved = []
    pipeline = StegoPipeline.__new__(StegoPipeline)
    pipeline.backend = SimpleNamespace(
        get_post_local=lambda filename, step: {
            "id": "p12",
            "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}],
        },
        save_object_local=lambda data, step, filename: saved.append((step, filename)),
    )
    pipeline.encode = lambda payload, post, tag: {
        "succeeded": False,
        "post": post,
        "stego_text": "candidate text",
        "error": "Decoding validation failed",
    }

    result = pipeline.process_post(post_id="p12", payload="x", tag="v1")

    assert result["succeeded"] is False
    assert saved == []


def test_process_post_skips_save_when_stego_text_empty_on_success():
    saved = []
    pipeline = StegoPipeline.__new__(StegoPipeline)
    pipeline.backend = SimpleNamespace(
        get_post_local=lambda filename, step: {
            "id": "p13",
            "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}],
        },
        save_object_local=lambda data, step, filename: saved.append((step, filename)),
    )
    pipeline.encode = lambda payload, post, tag: {
        "succeeded": True,
        "post": post,
        "stego_text": "",
    }

    result = pipeline.process_post(post_id="p13", payload="x", tag="v1")

    assert result["succeeded"] is True
    assert saved == []


def test_encode_extractive_zero_kld_embeds_large_hidden_payload(
    monkeypatch, clear_workflow_capacity_env
):
    monkeypatch.setenv("WORKFLOW_STEGO_GENERATION_MODE", "extractive_zero_kld")

    payload = "PAYLOAD-" + ("ABCD1234" * 512)
    visible_text = (
        "I keep the wording close to the original comments when I want distribution compatibility.\n"
        "That makes the text look natural because it is literally drawn from the same discussion."
    )
    post = {
        "id": "p-extractive",
        "title": "Extractive",
        "selftext": "",
        "comments": [
            {"id": "c1", "author": "alice", "body": visible_text.split("\n")[0], "replies": []},
            {"id": "c2", "author": "bob", "body": visible_text.split("\n")[1], "replies": []},
        ],
        "angles": [
            {
                "source_quote": "I keep the wording close to the original comments when I want distribution compatibility.",
                "tangent": "I keep the wording close to the original comments when I want distribution compatibility.",
                "category": "Community Discussion",
                "source_document": 0,
            }
        ],
    }

    result = StegoPipeline().encode(payload=payload, post=post, tag="version_test")

    assert result["succeeded"] is True
    assert result["stego_text"] == visible_text
    assert extract_invisible_payload(result["stego_text"]) is None
    assert_no_invisible_carrier(result["stego_text"])
    assert result["breakdown"]["embedded_payload_bits"] == len(payload.encode("utf-8")) * 8
    assert result["breakdown"]["payload_carrier"] == "selection_channel"


def test_security_profile_embeds_transformed_payload(monkeypatch, clear_workflow_capacity_env):
    monkeypatch.setenv("WORKFLOW_ENCODING_PROFILE", "security")
    monkeypatch.setenv("WORKFLOW_ENCODING_SECRET", "unit-test-secret")

    post = {
        "id": "p-secure",
        "title": "Secure",
        "selftext": "",
        "comments": [
            {
                "id": "c1",
                "author": "alice",
                "body": "This comment is stable source text for the extractive carrier.",
                "replies": [],
            }
        ],
        "angles": [
            {
                "source_quote": "This comment is stable source text for the extractive carrier.",
                "tangent": "This comment is stable source text for the extractive carrier.",
                "category": "Community Discussion",
                "source_document": 0,
            }
        ],
    }
    payload = "secret-value"

    result = StegoPipeline().encode(payload=payload, post=post, tag="version_security")
    embedded = extract_invisible_payload(result["stego_text"])

    assert result["succeeded"] is True
    assert embedded is None
    assert_no_invisible_carrier(result["stego_text"])
    assert result["embedding"]["compression"]["payload"] != payload
    assert result["embedding"]["compression"]["payload"].startswith("swsec2.")
    assert result["sender_audit"]["payload_transform"] == "secure_compact_v2"
    assert result["sender_audit"]["payload_carrier"] == "selection_channel"
