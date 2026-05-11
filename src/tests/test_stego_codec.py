"""Tests for shared stego codec (compress / embed / recover)."""

from workflows.utils.stego_codec import (
    angle_selection_bit_width,
    augment_post,
    augment_post_with_selection_bits,
    build_dictionary,
    comment_selection_bit_width,
    compress_payload,
    decompress_after_embed_prefix,
    embed_invisible_payload,
    extract_invisible_payload,
    protect_payload,
    recover_payload_bruteforce_comment_bits,
    recover_payload_with_compressed_full,
    strip_invisible_payload,
    unprotect_payload,
)


def test_compress_standard_empty_dictionary():
    r = compress_payload("abc", dictionary=[])
    assert r["method"] == "standard"
    assert r["compressed"].startswith("0")


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


def test_recover_with_compressed_full_accepts_modulo_angle_bits():
    post = {
        "id": "p-mod",
        "title": "",
        "selftext": "",
        "url": "https://example.com",
        "comments": [],
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


def test_legacy_invisible_payload_helpers_are_migration_only():
    visible_text = "Distribution-compatible visible text."
    payload = "hidden-" + ("XYZ123" * 256)

    stego_text = embed_invisible_payload(visible_text, payload)

    assert strip_invisible_payload(stego_text) == visible_text
    assert extract_invisible_payload(stego_text) == payload


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
