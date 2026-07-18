"""Receiver pipeline unit tests (mocked rebuild / decode)."""

from workflows.pipelines.receiver import (
    ReceiverPipeline,
    build_pre_sender_post,
    locate_sender_stego_comment,
    nested_angles_from_post,
)
from workflows.pipelines.stego import StegoPipeline
from workflows.utils.stego_codec import (
    augment_post,
    extract_invisible_payload,
)


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


def test_receiver_run_fails_fast_on_context_drift():
    pre_sender = {
        "id": "recv2",
        "title": "title",
        "selftext": "",
        "url": "https://example.com/article",
        "comments": [],
        "angles": [
            {"source_quote": "quote", "tangent": "tan", "category": "cat"},
        ],
    }
    full_post = dict(pre_sender)
    full_post["comments"] = [{"id": "stego_c", "author": "sender1", "body": "stego", "replies": []}]
    full_post["sender_audit"] = {
        "dictionary_hash": "not-the-rebuilt-hash",
        "angles_hash": "not-the-rebuilt-hash",
    }

    rp = ReceiverPipeline()
    rp.data_load.preview_post = lambda post, use_cache=True: {
        "post": {**post, "selftext": "fetched-body"},
        "report": {"fetch_success": True},
    }
    rp.research.preview_post = lambda post, force=True, **kwargs: {
        "post": {**post, "search_results": []},
        "report": {},
    }
    rp.gen_angles.preview_post = lambda post, allow_fallback=False: {
        "post": {
            **post,
            "angles": [{"source_quote": "quote", "tangent": "tan", "category": "cat"}],
            "options_count": 1,
        },
        "report": {},
    }

    out = rp.run(full_post, "sender1")
    assert out["succeeded"] is False
    assert out["stage"] == "context_drift"
    assert out["context_drift"]["status"] == "failed"
    assert out["context_drift"]["mismatches"]


def test_receiver_run_recovers_selection_channel_payload_from_sender_audit():
    pre_sender = {
        "id": "recv3",
        "title": "title",
        "selftext": "",
        "url": "https://example.com/article",
        "comments": [],
        "angles": [
            {
                "source_quote": "visible carrier text",
                "tangent": "visible carrier text",
                "category": "cat",
            },
        ],
    }
    aug = augment_post("legacy", pre_sender)
    angle_idx = int(aug["angleEmbedding"]["selectedAngle"]["idx"])
    stego_body = "visible carrier text"

    full_post = dict(pre_sender)
    full_post["sender_audit"] = {
        "selected_angle_index": angle_idx,
        "payload_transform": "plain",
        "compression": {"compressed": aug["compression"]["compressed"]},
    }
    full_post["comments"] = [
        {
            "id": "stego_c",
            "author": "sender1",
            "body": stego_body,
            "replies": [],
        }
    ]

    rp = ReceiverPipeline()
    rp.data_load.preview_post = lambda post, use_cache=True: {
        "post": {**post, "selftext": "fetched-body"},
        "report": {"fetch_success": True},
    }
    rp.research.preview_post = lambda post, force=True, **kwargs: {
        "post": {**post, "search_results": []},
        "report": {},
    }
    rp.gen_angles.preview_post = lambda post, allow_fallback=False: {
        "post": {**post, "angles": list(pre_sender["angles"]), "options_count": 1},
        "report": {},
    }
    rp.decode.decode = lambda **kwargs: angle_idx

    out = rp.run(
        full_post,
        "sender1",
        use_fetch_cache=False,
        use_terms_cache=False,
        persist_terms_cache=False,
        use_fetch_cache_research=False,
    )

    assert out["succeeded"] is True
    assert out["payload"] == "legacy"
    assert extract_invisible_payload(stego_body) is None
    assert out["recovery_meta"]["payload_carrier"] == "selection_channel"


def test_receiver_run_unwraps_security_profile_payload(monkeypatch, clear_workflow_capacity_env):
    monkeypatch.setenv("WORKFLOW_ENCODING_PROFILE", "security")
    monkeypatch.setenv("WORKFLOW_ENCODING_SECRET", "receiver-secret")
    pre_sender = {
        "id": "recv-sec",
        "title": "title",
        "selftext": "",
        "url": "https://example.com/article",
        "comments": [
            {
                "id": "c1",
                "author": "alice",
                "body": "visible carrier text for the secure profile",
                "replies": [],
            }
        ],
        "angles": [
            {
                "source_quote": "visible carrier text for the secure profile",
                "tangent": "visible carrier text for the secure profile",
                "category": "cat",
            },
        ],
    }
    secret = "payload-secure"
    encoded = StegoPipeline().encode(secret, pre_sender)
    angle_idx = int(encoded["angle_index"])

    full_post = dict(pre_sender)
    full_post["sender_audit"] = encoded["sender_audit"]
    full_post["comments"] = [
        *pre_sender["comments"],
        {"id": "stego_c", "author": "sender1", "body": encoded["stego_text"], "replies": []},
    ]

    rp = ReceiverPipeline()
    rp.data_load.preview_post = lambda post, use_cache=True: {
        "post": {**post, "selftext": "fetched-body"},
        "report": {"fetch_success": True},
    }
    rp.research.preview_post = lambda post, force=True, **kwargs: {
        "post": {**post, "search_results": []},
        "report": {},
    }
    rp.gen_angles.preview_post = lambda post, allow_fallback=False: {
        "post": {**post, "angles": list(pre_sender["angles"]), "options_count": 1},
        "report": {},
    }
    rp.decode.decode = lambda **kwargs: angle_idx

    out = rp.run(full_post, "sender1", fail_on_context_drift=False)

    assert out["succeeded"] is True
    assert out["payload"] == secret
    assert out["recovery_meta"]["payload_transform"] == "secure_compact_v2"


def test_receiver_decode_payload_canonicalizes_duplicate_angle_signature_index():
    duplicate_angle = {
        "source_quote": "duplicate quote",
        "tangent": "duplicate tangent",
        "category": "duplicate category",
    }
    pre_sender = {
        "id": "recv-dup",
        "title": "title",
        "selftext": "",
        "url": "https://example.com/article",
        "comments": [],
        "angles": [dict(duplicate_angle) for _ in range(10)],
    }
    secret = "payload-duplicate"
    aug = augment_post(secret, pre_sender)
    angle_idx = int(aug["angleEmbedding"]["selectedAngle"]["idx"])
    alt_idx = 0 if angle_idx != 0 else 1

    rp = ReceiverPipeline()
    rp.decode.decode = lambda **kwargs: alt_idx

    payload, info = rp.decode_payload(
        stego_text="synthetic stego comment body",
        rebuilt_post=pre_sender,
        pre_sender_post=pre_sender,
        nested_angles=nested_angles_from_post(pre_sender),
        compressed_full=aug["compression"]["compressed"],
        strict_mode=False,
        expected_angle_index=angle_idx,
    )

    assert payload == secret
    assert info["decoded_angle_index"] == angle_idx


def test_receiver_decode_payload_uses_sender_audit_index_when_semantic_decode_drifts():
    pre_sender = {
        "id": "recv-drift",
        "title": "title",
        "selftext": "",
        "url": "https://example.com/article",
        "comments": [],
        "angles": [
            {"source_quote": "quote a", "tangent": "tangent a", "category": "cat a"},
            {"source_quote": "quote b", "tangent": "tangent b", "category": "cat b"},
            {"source_quote": "quote c", "tangent": "tangent c", "category": "cat c"},
        ],
    }
    secret = "payload-drift"
    aug = augment_post(secret, pre_sender)
    angle_idx = int(aug["angleEmbedding"]["selectedAngle"]["idx"])
    alt_idx = next(idx for idx in range(len(pre_sender["angles"])) if idx != angle_idx)

    rp = ReceiverPipeline()
    rp.decode.decode = lambda **kwargs: alt_idx

    payload, info = rp.decode_payload(
        stego_text="synthetic stego comment body",
        rebuilt_post=pre_sender,
        pre_sender_post=pre_sender,
        nested_angles=nested_angles_from_post(pre_sender),
        compressed_full=aug["compression"]["compressed"],
        strict_mode=False,
        expected_angle_index=angle_idx,
    )

    assert payload == secret
    assert info["decoded_angle_index"] == angle_idx
    assert info["semantic_decoded_angle_index"] == alt_idx


def test_receiver_run_multi_frame_recovers_ordered_payload():
    sender = StegoPipeline.__new__(StegoPipeline)

    def encode(bits, post, tag=None, max_retries=0):
        full_bits = bits
        return {
            "succeeded": True,
            "stego_text": f"carrier-{full_bits}",
            "angle_index": int(full_bits[-2:] or "0", 2) % 3,
            "sender_audit": {"bits": full_bits},
        }

    sender.encode_binary_selection_bits = encode
    posts = []
    for idx in range(40):
        posts.append(
            {
                "id": f"post{idx + 1}",
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
    encoded = sender.encode_payload_frames("hi", posts, max_frames_per_post=4)

    rp = ReceiverPipeline()
    rp.decode.decode = lambda **kwargs: (
        int(
            str(kwargs["stego_text"]).split("carrier-", 1)[1][-2:] or "0",
            2,
        )
        % 3
    )

    out = rp.run_multi_frame(
        encoded["posts"],
        "sender",
        ordered_frame_refs=encoded["ordered_frame_refs"],
        payload_transform="plain",
    )

    assert out["succeeded"] is True
    assert out["payload"] == "hi"
    assert out["frame_count"] == len(encoded["frames"])


def test_receiver_multi_frame_rejects_invalid_ordered_reference():
    receiver = ReceiverPipeline.__new__(ReceiverPipeline)
    receiver._collect_multi_frame_frames = lambda *args: [
        {"post_id": "p1", "comment_id": "c1", "failed": True, "error": "invalid_frame_reference"}
    ]

    out = receiver.run_multi_frame(
        [], "sender", ordered_frame_refs=[{"post_id": "p1", "comment_id": "c1"}]
    )

    assert out["succeeded"] is False
    assert out["error"] == "Could not reconstruct a valid compact multi-frame payload"
