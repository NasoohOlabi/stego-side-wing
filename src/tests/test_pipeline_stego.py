from types import SimpleNamespace

import pytest

from workflows.pipelines import stego
from workflows.pipelines.stego import StegoPipeline
from workflows.utils.output_results_shape import n8n_save_object_body


def test_n8n_save_object_body_legacy_shape():
    body = n8n_save_object_body(
        {"stego_text": "x", "embedding": {"a": 1}, "post": {"id": "1"}}
    )
    assert body == [{"stegoText": "x", "embedding": {"a": 1}, "post": {"id": "1"}}]
    assert n8n_save_object_body({}) == [
        {"stegoText": "", "embedding": None, "post": None}
    ]


def test_stego_helpers_cover_edge_cases():
    assert stego._is_non_empty_string("x") is True
    assert stego._is_non_empty_string("") is False
    assert stego._get_bit_width(0) == 1
    assert stego._get_bit_width(8) >= 1
    taken, remaining, insufficient = stego._take_bits("101", 5)
    assert taken == "10100"
    assert remaining == ""
    assert insufficient is True


def test_stego_comment_strings_from_parsed_requires_three_strings() -> None:
    assert stego._stego_comment_strings_from_parsed(["a", "b", "c"]) == ["a", "b", "c"]
    assert stego._stego_comment_strings_from_parsed(["a", "b"]) is None
    assert stego._stego_comment_strings_from_parsed(["a", "b", "c", "d"]) is None
    assert stego._stego_comment_strings_from_parsed(
        {"texts": ["x", "y", "z"]}
    ) == ["x", "y", "z"]
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
    pipeline._generate_stego_texts = lambda sample, comment_embedding: ["candidate text"]
    pipeline._cross_validate = lambda **kwargs: {
        "succeeded": True,
        "stegoText": "candidate text",
        "decodedIndices": [2],
    }

    post = {"id": "p1", "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}]}
    result = pipeline.encode(payload="secret", post=post, tag="tag")

    assert result["succeeded"] is True
    assert result["stego_text"] == "candidate text"
    assert result["angle_index"] == 2


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
    pipeline.encode = lambda payload, post, tag: {"succeeded": True, "post": post, "stego_text": "ok"}

    result = pipeline.process_post(post_id="p9", payload="x", tag="v1", step="final-step")

    assert result["succeeded"] is True
    assert calls == [("final-step", "p9_v1.json", True)]


def test_process_post_auto_selects_next_unprocessed_post_with_tag():
    calls = []
    selected = {}
    pipeline = StegoPipeline.__new__(StegoPipeline)

    def fake_posts_list(step, count, offset, tag):
        selected.update(
            {"step": step, "count": count, "offset": offset, "tag": tag}
        )
        return {"fileNames": ["p10.json"]}

    pipeline.backend = SimpleNamespace(
        posts_list=fake_posts_list,
        get_post_local=lambda filename, step: {
            "id": "p10",
            "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}],
        },
        save_object_local=lambda data, step, filename: calls.append((step, filename)),
    )
    pipeline._load_default_payload_and_tag = lambda: ("default payload", "same-tag")
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
    pipeline._load_default_payload_and_tag = lambda: ("default payload", "same-tag")
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


def test_cross_validate_rejects_empty_matching_candidate_text():
    pipeline = StegoPipeline.__new__(StegoPipeline)
    angle = {"category": "c", "tangent": "t", "source_quote": "q"}
    pipeline.decode_pipeline = SimpleNamespace(decode=lambda **kwargs: 0)

    result = pipeline._cross_validate(
        candidate_texts=[""],
        few_shots=[],
        tangents_db=[angle],
        selected_angle=angle,
    )

    assert result["succeeded"] is False


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
