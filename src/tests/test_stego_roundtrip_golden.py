"""Characterization tests pinning the sender/receiver codec contract.

The other codec tests assert on individual fields, so a refactor can rename a key,
reorder a list or shift a bit width without any of them failing. These tests pin the
*whole* serialized augmentation against a golden fixture, and then prove the receiver
still recovers the payload from it.

This is the safety net for the maintainability refactor: any behavioural drift in
``stego_codec`` shows up here as a diff, not as a silent change in run artifacts.

Regenerate the fixture **only** when a change to the contract is intended, and review
the resulting diff as carefully as the code change:

    STEGO_GOLDEN_REGEN=1 uv run python -m pytest -q src/tests/test_stego_roundtrip_golden.py

The capacity-env fixture matters: ``augment_post`` builds its dictionary through the
capacity profile, so a developer ``.env`` would otherwise change the golden output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from workflows.utils.stego_codec import (
    augment_post,
    augment_post_with_recoverable_selection_bits,
    build_dictionary,
    flatten_comments,
    recover_payload_bruteforce_comment_bits,
    recover_payload_with_compressed_full,
    recoverable_frame_bit_candidates_from_observations,
    recoverable_selection_channel_capacity,
    selection_channel_capacity_report,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
AUGMENT_GOLDEN = GOLDEN_DIR / "stego_augment_post.json"

GOLDEN_PAYLOAD = "meet at the usual place"

# A fixed, realistic post: nested comment replies, several angles, and search results,
# so the dictionary, comment-selection and angle-selection channels are all exercised.
GOLDEN_POST: dict[str, Any] = {
    "id": "golden-post-1",
    "title": "City council approves the new transit line",
    "selftext": "The vote was 7-2. Construction is expected to start next spring.",
    "url": "https://example.com/transit",
    "author": "op_user",
    "comments": [
        {
            "id": "c1",
            "author": "alice",
            "body": "Finally. The bus route down 5th has been overloaded for years.",
            "replies": [
                {
                    "id": "c1r1",
                    "author": "bob",
                    "body": "Agreed, though the timeline seems optimistic.",
                    "replies": [],
                }
            ],
        },
        {
            "id": "c2",
            "author": "carol",
            "body": "Curious how they are funding the tunnelling section.",
            "replies": [],
        },
    ],
    "angles": [
        {"source_quote": "vote was 7-2", "tangent": "council margin", "category": "politics"},
        {
            "source_quote": "start next spring",
            "tangent": "construction timeline",
            "category": "infrastructure",
        },
        {
            "source_quote": "new transit line",
            "tangent": "transit coverage",
            "category": "infrastructure",
        },
    ],
    "search_results": [
        {
            "title": "Transit expansion plans",
            "link": "https://example.com/a",
            "snippet": "The council margin was narrow.",
        },
        {
            "title": "Tunnelling costs",
            "link": "https://example.com/b",
            "snippet": "Funding for the tunnelling section.",
        },
    ],
}


def _nested_angles(post: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return [[angle] for angle in post["angles"]]


def _load_or_write_golden(path: Path, actual: dict[str, Any]) -> dict[str, Any]:
    """Return the stored golden, writing it first when regeneration is requested."""
    if os.environ.get("STEGO_GOLDEN_REGEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    if not path.exists():
        pytest.fail(
            f"Missing golden fixture {path}. Regenerate with STEGO_GOLDEN_REGEN=1 "
            f"and review the produced file before committing."
        )
    # Compare parsed objects rather than raw text so CRLF checkouts do not fail.
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_augment_post_matches_golden_artifact() -> None:
    actual = augment_post(GOLDEN_PAYLOAD, GOLDEN_POST)
    expected = _load_or_write_golden(AUGMENT_GOLDEN, actual)

    # Compare the whole structure; a per-key loop would report the first diff only.
    assert actual == expected


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_augment_post_is_deterministic() -> None:
    first = augment_post(GOLDEN_PAYLOAD, GOLDEN_POST)
    second = augment_post(GOLDEN_PAYLOAD, GOLDEN_POST)

    assert first == second


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_receiver_recovers_payload_from_sender_augmentation() -> None:
    """Sender produces the augmentation -> receiver recovers the exact payload."""
    augmentation = augment_post(GOLDEN_PAYLOAD, GOLDEN_POST)
    compressed_full = augmentation["compression"]["compressed"]
    angle_index = int(augmentation["angleEmbedding"]["selectedAngle"]["idx"])
    dictionary = build_dictionary(GOLDEN_POST)
    nested = _nested_angles(GOLDEN_POST)

    with_full = recover_payload_with_compressed_full(
        compressed_full, dictionary, GOLDEN_POST, nested, decoded_angle_index=angle_index
    )
    bruteforce = recover_payload_bruteforce_comment_bits(
        dictionary,
        GOLDEN_POST,
        nested,
        decoded_angle_index=angle_index,
        max_padding_bits=256,
        compressed_full=compressed_full,
    )

    assert with_full is not None, "receiver could not recover with the known compressed bits"
    assert bruteforce is not None, "receiver could not recover by brute-forcing comment bits"
    assert with_full[0] == GOLDEN_PAYLOAD
    assert bruteforce[0] == GOLDEN_PAYLOAD


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_recoverable_selection_bits_round_trip_is_exhaustive_and_unambiguous() -> None:
    """Every bit pattern the recoverable channel accepts must decode back uniquely.

    Note the physical and recoverable widths differ: with 3 angles the angle channel
    writes a 2-bit index but only carries 1 recoverable bit, because a 2-bit index over
    3 choices would alias under the modulo. So the invariant is a round-trip through
    the observable selections, not concatenation of ``commentBits`` and ``angleBits``.
    """
    capacity = recoverable_selection_channel_capacity(GOLDEN_POST)
    assert capacity == 3

    n_angles = len(GOLDEN_POST["angles"])
    for value in range(2**capacity):
        bits = format(value, f"0{capacity}b")
        augmentation = augment_post_with_recoverable_selection_bits(bits, GOLDEN_POST)

        chain = augmentation["commentEmbedding"]["pickedCommentChain"]
        parent_id = chain[-1]["id"] if chain else None
        angle_index = int(augmentation["angleEmbedding"]["selectedAngle"]["idx"])

        candidates = recoverable_frame_bit_candidates_from_observations(
            post=GOLDEN_POST,
            parent_id=parent_id,
            decoded_angle_index=angle_index,
            n_angles=n_angles,
        )

        assert candidates == [bits], (
            f"recoverable channel must round-trip {bits!r} uniquely, got {candidates!r}"
        )


@pytest.mark.usefixtures("clear_workflow_capacity_env")
def test_capacity_report_agrees_with_embedded_widths() -> None:
    report = selection_channel_capacity_report(GOLDEN_POST)
    augmentation = augment_post(GOLDEN_PAYLOAD, GOLDEN_POST)

    # Comment choices count *flattened* comments (nested replies included) plus the
    # "select no comment" state.
    assert report["comment_choices"] == len(flatten_comments(GOLDEN_POST["comments"])) + 1
    assert report["tangent_choices"] == len(GOLDEN_POST["angles"])

    assert augmentation["commentEmbedding"]["bitsCount"] == report["comment_physical_width"]
    assert augmentation["angleEmbedding"]["bitsCount"] == report["tangent_physical_width"]

    # The tangent channel loses a bit to modulo aliasing; the comment channel does not.
    assert report["comment_recoverable_bits"] == report["comment_physical_width"]
    assert report["tangent_recoverable_bits"] < report["tangent_physical_width"]
    assert (
        report["recoverable_capacity_bits"]
        == report["comment_recoverable_bits"] + report["tangent_recoverable_bits"]
    )
