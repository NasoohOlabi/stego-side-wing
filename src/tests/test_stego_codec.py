"""Tests for shared stego codec (compress / embed / recover)."""

import pytest

from workflows.utils.stego_codec import (
    INVISIBLE_PAYLOAD_END,
    INVISIBLE_PAYLOAD_LENGTH_BITS,
    INVISIBLE_PAYLOAD_ONE,
    INVISIBLE_PAYLOAD_START,
    INVISIBLE_PAYLOAD_ZERO,
    angle_selection_bit_width,
    augment_post,
    augment_post_with_selection_bits,
    build_dictionary,
    build_multi_frame_stream,
    comment_selection_bit_width,
    compress_payload,
    decode_elias_gamma,
    decompress_after_embed_prefix,
    encode_elias_gamma,
    encode_int,
    extract_invisible_payload,
    frame_bits_across_posts,
    frame_payload_bits,
    get_bit_width,
    parse_framed_payload_bits,
    parse_multi_frame_stream,
    protect_payload,
    recover_bits_from_post_frames,
    recover_payload_bruteforce_comment_bits,
    recover_payload_with_compressed_full,
    recoverable_frame_bit_candidates_from_observations,
    recoverable_selection_channel_capacity,
    selection_channel_capacity,
    selection_channel_capacity_report,
    strip_invisible_payload,
    unprotect_payload,
)


def test_compress_standard_empty_dictionary():
    r = compress_payload("abc", dictionary=[])
    assert r["method"] == "standard"
    assert r["compressed"].startswith("0")


def test_zero_width_selection_capacity_uses_no_bits():
    assert get_bit_width(0) == 0
    assert encode_int(0, 0) == ""
    assert angle_selection_bit_width(1) == 0


def test_decompress_roundtrip_standard_no_comments():
    # Empty selftext / no snippets so ``build_dictionary`` is empty → standard compression.
    post = {
        "id": "p1",
        "title": "t",
        "selftext": "",
        "url": "https://example.com",
        "comments": [],
        "angles": [{"source_quote": "q", "tangent": "tan", "category": "c"}],
    }
    payload = "secret-payload"
    aug = augment_post(payload, post)
    comp = aug["compression"]["compressed"]
    lc = comment_selection_bit_width(post)
    la = aug["angleEmbedding"]["bitsCount"]
    assert len(comp) >= lc + la
    recovered = decompress_after_embed_prefix(comp, build_dictionary(post), lc, la)
    assert recovered == payload


def test_recover_with_compressed_full_matches_bruteforce():
    post = {
        "id": "p1",
        "title": "t",
        "selftext": "",
        "url": "https://example.com",
        "comments": [],
        "angles": [
            {"source_quote": "q1", "tangent": "t1", "category": "c1"},
            {"source_quote": "q2", "tangent": "t2", "category": "c2"},
        ],
    }
    payload = "x"
    aug = augment_post(payload, post)
    comp = aug["compression"]["compressed"]
    nested = [[a] for a in post["angles"]]
    dictionary = build_dictionary(post)
    idx = int(aug["angleEmbedding"]["selectedAngle"]["idx"])

    full = recover_payload_with_compressed_full(
        comp, dictionary, post, nested, decoded_angle_index=idx
    )
    brute = recover_payload_bruteforce_comment_bits(
        dictionary,
        post,
        nested,
        decoded_angle_index=idx,
        max_padding_bits=256,
        compressed_full=comp,
    )
    assert full is not None and brute is not None
    assert full[0] == payload == brute[0]


def test_augment_post_with_selection_bits_selects_comment_and_angle_without_compression():
    post = {
        "id": "p-bits",
        "title": "t",
        "selftext": "",
        "comments": [
            {"id": "c1", "author": "a", "body": "one", "replies": []},
            {"id": "c2", "author": "b", "body": "two", "replies": []},
        ],
        "angles": [
            {"source_quote": "q0", "tangent": "t0", "category": "c0"},
            {"source_quote": "q1", "tangent": "t1", "category": "c1"},
            {"source_quote": "q2", "tangent": "t2", "category": "c2"},
        ],
    }

    aug = augment_post_with_selection_bits("1001", post)
    normal = augment_post("101", post)

    assert aug["compression"]["method"] == "diagnostic_binary_selection_bits"
    assert aug["compression"]["compressionSkipped"] is True
    assert aug["diagnostic"]["payload_transform_skipped"] is True
    assert aug["commentBits"] == "10"
    assert aug["angleBits"] == "01"
    assert aug["commentEmbedding"]["pickedCommentChain"][-1]["id"] == "c2"
    assert aug["angleEmbedding"]["selectedAngle"]["idx"] == 1
    assert normal["compression"]["method"] != "diagnostic_binary_selection_bits"


def test_frame_bits_across_posts_splits_and_reassembles_arbitrary_length_bits():
    posts = []
    for idx in range(4):
        posts.append(
            {
                "id": f"p{idx + 1}",
                "comments": [
                    {"id": "c1", "author": "a", "body": "one", "replies": []},
                    {"id": "c2", "author": "b", "body": "two", "replies": []},
                ],
                "angles": [
                    {"source_quote": "q1", "tangent": "t1", "category": "c1"},
                    {"source_quote": "q2", "tangent": "t2", "category": "c2"},
                    {"source_quote": "q3", "tangent": "t3", "category": "c3"},
                ],
            }
        )
    bits = "1010110011110001"

    framed = frame_bits_across_posts(bits, posts)

    assert framed["originalBits"] == bits
    assert framed["remainingBits"] == ""
    assert len(framed["frames"]) == 4
    assert [frame["frameIndex"] for frame in framed["frames"]] == [0, 1, 2, 3]
    assert recover_bits_from_post_frames(framed["frames"]) == bits


def test_frame_payload_bits_round_trip_and_padding_trim():
    payload_bits = "1" * 257
    framed = frame_payload_bits(payload_bits)

    parsed = parse_framed_payload_bits(framed + "0000")

    assert parsed["payload_bits"] == payload_bits
    assert parsed["payload_bit_length"] == len(payload_bits)
    assert parsed["padding_bits"] == "0000"


def test_compact_multi_frame_stream_round_trip_and_padding_trim():
    payload_bits = "101010100"
    stream = build_multi_frame_stream(payload_bits, frame_count=7)

    parsed = parse_multi_frame_stream(stream["stream_bits"] + "000", expected_frame_count=7)

    assert parsed is not None
    assert parsed["payload_bits"] == payload_bits
    assert parsed["padding_bits"] == "000"
    assert decode_elias_gamma(encode_elias_gamma(7)) == (7, 5)


def test_compact_multi_frame_stream_rejects_wrong_count_and_nonzero_padding():
    stream = build_multi_frame_stream("101", frame_count=2)["stream_bits"]

    assert parse_multi_frame_stream(stream, expected_frame_count=3) is None
    assert parse_multi_frame_stream(stream + "1", expected_frame_count=2) is None


def test_parse_framed_payload_bits_rejects_bad_magic_and_checksum():
    framed = frame_payload_bits("10101010")
    with_bad_magic = "0" + framed[1:]
    with_bad_checksum = framed[:-1] + ("1" if framed[-1] == "0" else "0")

    with pytest.raises(ValueError, match="magic"):
        parse_framed_payload_bits(with_bad_magic)

    with pytest.raises(ValueError, match="checksum"):
        parse_framed_payload_bits(with_bad_checksum)


def test_selection_channel_capacity_matches_selected_embedding_width():
    post = {
        "id": "p-cap",
        "comments": [
            {"id": "c1", "author": "a", "body": "one", "replies": []},
            {"id": "c2", "author": "b", "body": "two", "replies": []},
        ],
        "angles": [
            {"source_quote": "q1", "tangent": "t1", "category": "c1"},
            {"source_quote": "q2", "tangent": "t2", "category": "c2"},
            {"source_quote": "q3", "tangent": "t3", "category": "c3"},
        ],
    }

    assert selection_channel_capacity(post) == 4


def test_recoverable_selection_capacity_excludes_modulo_aliases():
    post = {
        "id": "p-safe-cap",
        "comments": [
            {"id": "c1", "author": "a", "body": "one", "replies": []},
            {"id": "c2", "author": "b", "body": "two", "replies": []},
        ],
        "angles": [
            {"source_quote": "q1", "tangent": "t1", "category": "c1"},
            {"source_quote": "q2", "tangent": "t2", "category": "c2"},
            {"source_quote": "q3", "tangent": "t3", "category": "c3"},
        ],
    }

    assert recoverable_selection_channel_capacity(post) == 2
    assert recoverable_frame_bit_candidates_from_observations(
        post=post, parent_id="c1", decoded_angle_index=1, n_angles=3
    ) == ["11"]


@pytest.mark.parametrize(
    ("comment_count", "tangent_count", "expected"),
    [(0, 0, 0), (1, 1, 1), (2, 3, 2), (3, 4, 4), (7, 9, 6)],
)
def test_capacity_report_tracks_dynamic_comment_and_tangent_counts(
    comment_count: int, tangent_count: int, expected: int
) -> None:
    post = {
        "id": "dynamic",
        "comments": [
            {"id": f"c{i}", "author": "a", "body": "body", "replies": []}
            for i in range(comment_count)
        ],
        "angles": [
            {"source_quote": f"q{i}", "tangent": f"t{i}", "category": "c"}
            for i in range(tangent_count)
        ],
    }

    report = selection_channel_capacity_report(post)

    assert report["comment_choices"] == comment_count + 1
    assert report["tangent_choices"] == tangent_count
    assert report["recoverable_capacity_bits"] == expected
    assert recoverable_selection_channel_capacity(post) == expected


def test_recover_with_compressed_full_accepts_modulo_angle_bits():
    post = {
        "id": "p-mod",
        "title": "",
        "selftext": "",
        "url": "https://example.com",
        "comments": [{"id": "c1", "author": "a", "body": "body", "replies": []}],
        "angles": [
            {"source_quote": "q1", "tangent": "t1", "category": "c1"},
            {"source_quote": "q2", "tangent": "t2", "category": "c2"},
            {"source_quote": "q3", "tangent": "t3", "category": "c3"},
        ],
    }
    payload = "\u00e9"
    aug = augment_post(payload, post)
    comp = aug["compression"]["compressed"]
    nested = [[a] for a in post["angles"]]
    dictionary = build_dictionary(post)

    assert comp[1:3] == "11"
    recovered = recover_payload_with_compressed_full(
        comp, dictionary, post, nested, decoded_angle_index=0
    )

    assert recovered is not None
    assert recovered[0] == payload
    assert recovered[1]["angle_bits"] == "11"


def _build_legacy_invisible_text(visible_text: str, payload: str) -> str:
    """Construct a legacy invisible-carrier artifact.

    The production write-side helper was removed: AGENTS.md forbids invisible carriers, so
    the codec must not offer a way to create one. This local builder keeps the read-side
    (detect / strip) covered against the exact byte layout legacy artifacts use.
    """
    payload_bytes = payload.encode("utf-8")
    length_bits = format(len(payload_bytes), f"0{INVISIBLE_PAYLOAD_LENGTH_BITS}b")
    payload_bits = "".join(format(b, "08b") for b in payload_bytes)
    invisible_bits = "".join(
        INVISIBLE_PAYLOAD_ONE if bit == "1" else INVISIBLE_PAYLOAD_ZERO
        for bit in length_bits + payload_bits
    )
    return f"{visible_text}{INVISIBLE_PAYLOAD_START}{invisible_bits}{INVISIBLE_PAYLOAD_END}"


def test_legacy_invisible_payload_helpers_are_read_only():
    visible_text = "Distribution-compatible visible text."
    payload = "hidden-" + ("XYZ123" * 256)

    stego_text = _build_legacy_invisible_text(visible_text, payload)

    assert strip_invisible_payload(stego_text) == visible_text
    assert extract_invisible_payload(stego_text) == payload


def test_codec_exposes_no_invisible_carrier_write_helper():
    """Guard the forbidden-carrier rule: nothing in the codec may create invisible payloads."""
    import workflows.utils.stego_codec as codec

    writers = [
        name
        for name in dir(codec)
        if "invisible" in name.lower() and name.startswith(("embed", "encode", "build", "make"))
    ]
    assert writers == []


def test_secure_payload_transform_roundtrip_and_authentication():
    payload = "sensitive payload"
    protected = protect_payload(payload, transform="hmac_xor_v1", secret="test-secret")

    assert protected != payload
    assert unprotect_payload(protected, transform="hmac_xor_v1", secret="test-secret") == payload
    assert unprotect_payload(protected + "x", transform="hmac_xor_v1", secret="test-secret") is None
    assert unprotect_payload(protected, transform="hmac_xor_v1", secret="wrong") is None


def test_secure_compact_v2_roundtrip_sizes_and_authentication():
    for size in (16, 49, 96, 512, 2048):
        payload = "x" * size
        protected = protect_payload(
            payload,
            transform="secure_compact_v2",
            secret="test-secret",
        )

        assert protected.startswith("swsec2.")
        assert protected != payload
        assert (
            unprotect_payload(
                protected,
                transform="secure_compact_v2",
                secret="test-secret",
            )
            == payload
        )
        assert (
            unprotect_payload(
                protected + "x",
                transform="secure_compact_v2",
                secret="test-secret",
            )
            is None
        )
        assert (
            unprotect_payload(
                protected,
                transform="secure_compact_v2",
                secret="wrong",
            )
            is None
        )
