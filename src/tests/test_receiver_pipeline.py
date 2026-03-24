"""Receiver pipeline unit tests (mocked rebuild / decode)."""

from workflows.pipelines.receiver import (
    build_pre_sender_post,
    locate_sender_stego_comment,
)
from workflows.pipelines.receiver import ReceiverPipeline
from workflows.utils.stego_codec import augment_post


def test_locate_sender_stego_comment():
    post = {
        "id": "p1",
        "comments": [
            {"id": "c1", "author": "alice", "body": "plain"},
            {"id": "c2", "author": "bob", "body": "stego text here"},
        ],
    }
    found = locate_sender_stego_comment(post, "bob")
    assert found is not None
    assert found["id"] == "c2"


def test_build_pre_sender_post_removes_comment():
    post = {
        "id": "p1",
        "comments": [
            {
                "id": "root",
                "author": "u",
                "body": "x",
                "replies": [{"id": "child", "author": "bob", "body": "secret", "replies": []}],
            }
        ],
    }
    stripped = build_pre_sender_post(post, "child")
    flat = stripped["comments"][0]["replies"]
    assert flat == []


def test_receiver_run_with_mocks():
    pre_sender = {
        "id": "recv1",
        "title": "title",
        "selftext": "",
        "url": "https://example.com/article",
        "comments": [],
        "angles": [
            {"source_quote": "quote", "tangent": "tan", "category": "cat"},
        ],
    }
    secret = "payload-42"
    aug = augment_post(secret, pre_sender)
    angle_idx = int(aug["angleEmbedding"]["selectedAngle"]["idx"])
    compressed = aug["compression"]["compressed"]
    stego_body = "synthetic stego comment body"

    full_post = dict(pre_sender)
    full_post["comments"] = [
        {
            "id": "stego_c",
            "author": "sender1",
            "body": stego_body,
            "replies": [],
        }
    ]

    rp = ReceiverPipeline()

    rebuilt = {
        **pre_sender,
        "selftext": "",
        "search_results": [],
        "angles": list(pre_sender["angles"]),
        "options_count": 1,
    }

    rp.data_load.preview_post = lambda post, use_cache=True: {
        "post": {**post, "selftext": "fetched-body"},
        "report": {"fetch_success": True},
    }
    rp.research.preview_post = lambda post, force=True, **kwargs: {
        "post": {**post, "search_results": rebuilt["search_results"]},
        "report": {},
    }
    rp.gen_angles.preview_post = lambda post, allow_fallback=False: {
        "post": {**post, "angles": rebuilt["angles"], "options_count": 1},
        "report": {},
    }
    rp.decode.decode = lambda **kwargs: angle_idx

    out = rp.run(
        full_post,
        "sender1",
        compressed_full=compressed,
        use_fetch_cache=False,
        use_terms_cache=False,
        persist_terms_cache=False,
        use_fetch_cache_research=False,
    )
    assert out["succeeded"] is True
    assert out["payload"] == secret
    assert out["decoded_angle_index"] == angle_idx
