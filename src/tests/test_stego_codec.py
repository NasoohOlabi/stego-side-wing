"""Tests for shared stego codec (compress / embed / recover)."""

from workflows.utils.stego_codec import (
    angle_selection_bit_width,
    augment_post,
    build_dictionary,
    comment_selection_bit_width,
    compress_payload,
    decompress_after_embed_prefix,
    recover_payload_bruteforce_comment_bits,
    recover_payload_with_compressed_full,
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
