"""Build the sender-side audit trail attached to an encoded post.

Split out of ``stego.py`` (plan step 3.2). ``sender_audit_from_post`` builds the base
``SenderAudit`` fields; ``StegoPipeline.encode``/``encode_binary_selection_bits`` mutate
the returned dict further as later stages complete -- see ``SenderAudit``'s docstring in
``workflows/contracts.py`` for why that stays a plain mutable dict.
"""

from typing import Any, cast

from workflows.contracts import PostAugmentation, SenderAudit
from workflows.utils.protocol_utils import stable_hash
from workflows.utils.stego_codec import build_dictionary_report as codec_build_dictionary_report


def tangent_db_report_field(post: dict[str, Any]) -> dict[str, Any]:
    """Echo the persisted v1 tangent-DB build report into the audit when the post carries one."""
    report = post.get("tangent_db_report")
    return {"tangent_db_report": report} if isinstance(report, dict) else {}


def sender_audit_from_post(
    post: dict[str, Any], post_augmentation: PostAugmentation
) -> SenderAudit:
    dictionary_report = codec_build_dictionary_report(post)
    tangents_db = list(post_augmentation.get("angleEmbedding", {}).get("TangentsDB", []))
    search_results = list(post.get("search_results", []) or [])
    selected_urls_hashes = [stable_hash(item) for item in search_results]
    compression = dict(post_augmentation.get("compression", {}))
    # A dict literal with a trailing ``**`` spread can't be checked structurally against a
    # TypedDict, so pyright falls back to inferring a plain dict here -- cast documents the
    # contract without changing how the dict is actually built.
    return cast(
        SenderAudit,
        {
            "dictionary_id": dictionary_report["dictionary_id"],
            "dictionary_hash": dictionary_report["texts_hash"],
            "dictionary_count": dictionary_report["entry_count"],
            "angles_hash": stable_hash(tangents_db),
            "angles_count": len(tangents_db),
            "selected_url_hashes": selected_urls_hashes,
            "selected_urls_hash": stable_hash(selected_urls_hashes),
            "selected_angle_index": post_augmentation.get("angleEmbedding", {})
            .get("selectedAngle", {})
            .get("idx"),
            "compression": {
                "method": compression.get("method"),
                "compressed": compression.get("compressed"),
                "compressed_length": compression.get("compressedLength"),
                "original_length": compression.get("originalLength"),
                "compressed_hash": stable_hash(compression.get("compressed", "")),
            },
            "selection_signature": post_augmentation.get("selectionSignature"),
            "comment_bits": post_augmentation.get("commentBits"),
            "angle_bits": post_augmentation.get("angleBits"),
            **tangent_db_report_field(post),
        },
    )
