"""Typed payloads passed between workflow stages."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict


class SenderAuditCompression(TypedDict, total=False):
    """The ``compression`` sub-dict inside :class:`SenderAudit`."""

    method: str | None
    compressed: str | None
    compressed_length: int | None
    original_length: int | None
    compressed_hash: str | None


class SenderAudit(TypedDict):
    """Sender-side audit trail attached to an encoded post.

    A ``TypedDict``, not a ``BaseModel``: ``_sender_audit_from_post`` builds the required
    fields below, then ``StegoPipeline.encode``/``encode_binary_selection_bits`` mutate the
    same dict in place across ~350 lines each, adding the ``NotRequired`` fields as later
    stages complete (timings, candidate validation, diagnostic flags). That mutation is
    load-bearing -- a frozen or validated model would force restructuring both methods as a
    side effect of typing their contract. This still gives pyright (strict on ``workflows``)
    a fixed key set to check the ~20 read/write call sites in ``stego.py`` and
    ``receiver.py`` against.
    """

    dictionary_id: str
    dictionary_hash: str
    dictionary_count: int
    angles_hash: str
    angles_count: int
    selected_url_hashes: list[str]
    selected_urls_hash: str
    selected_angle_index: int | None
    compression: SenderAuditCompression
    selection_signature: str | None
    comment_bits: str | None
    angle_bits: str | None
    tangent_db_report: NotRequired[dict[str, Any]]
    encoding: NotRequired[dict[str, Any]]
    payload_transform: NotRequired[str]
    payload_carrier: NotRequired[str]
    raw_payload_bytes: NotRequired[int]
    payload_bytes: NotRequired[int]
    embedded_payload_bytes: NotRequired[int]
    llm_timings: NotRequired[list[dict[str, Any]]]
    candidate_validation: NotRequired[dict[str, Any]]
    binary_selection_bits: NotRequired[str]
    compression_skipped: NotRequired[bool]
    payload_transform_skipped: NotRequired[bool]


class PostAugmentation(TypedDict):
    """Selection-channel embedding result for one post.

    Produced by ``stego_codec.augment_post`` and its selection-bits diagnostic variants,
    which always set every required field below; ``StegoPipeline`` then mutates the same
    dict once to attach ``senderAudit``, and the multi-frame helpers in ``stego_codec.py``
    add their own ``NotRequired`` frame-tracking fields. ``commentEmbedding``/
    ``angleEmbedding`` stay as ``dict[str, Any]``: they carry angle/comment payloads sourced
    from post content and LLM output, not a fixed shape this contract can pin down without
    risking silently rejecting real data. See :class:`SenderAudit` for why this is a
    ``TypedDict`` rather than a ``BaseModel``.
    """

    compression: dict[str, Any]
    commentEmbedding: dict[str, Any]
    angleEmbedding: dict[str, Any]
    totalBitsEmbedded: int
    fullEncodedBits: str
    commentBits: str
    angleBits: str
    selectionSignature: str
    warnings: list[str]
    # Only ``augment_post`` sets this; the selection-bits diagnostic variants do not.
    remainingBitsUnembedded: NotRequired[int]
    diagnostic: NotRequired[dict[str, Any]]
    recoverableBits: NotRequired[str]
    senderAudit: NotRequired[SenderAudit]
    # Set only by stego_codec.frame_bits_across_posts, one multi-frame diagnostic per post.
    frameIndex: NotRequired[int]
    postId: NotRequired[Any]
    selectionChannelCapacity: NotRequired[int]


class FetchUrlResult(BaseModel):
    """Outcome of fetching and extracting one URL.

    Frozen: stages pass this along and branch on it, but nothing mutates an instance --
    ``FetchUrlContentPipeline`` builds a new one per attempt rather than editing in place.
    """

    model_config = ConfigDict(frozen=True)

    url: str
    success: bool
    text: str | None = None
    content_type: str | None = None
    error: str | None = None
