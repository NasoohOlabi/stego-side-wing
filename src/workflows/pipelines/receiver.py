"""Receiver pipeline: rebuild post context and recover stego payload."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from loguru import logger

from infrastructure.config import (
    get_workflow_context_sampler,
    get_workflow_decode_strict_default,
    get_workflow_encoding_secret,
    get_workflow_payload_transform,
)
from workflows.contracts import SenderAudit
from workflows.pipelines.data_load import DataLoadPipeline
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.research import ResearchPipeline
from workflows.utils.protocol_utils import (
    angle_signature as _angle_signature,
)
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.stego_codec import (
    build_dictionary,
    build_dictionary_report,
    decode_elias_gamma,
    flatten_nested_angles,
    from_binary_utf8,
    parse_multi_frame_stream,
    recover_payload_bruteforce_comment_bits,
    recover_payload_with_compressed_full,
    recoverable_frame_bit_candidates_from_observations,
    unprotect_payload,
)
from workflows.utils.text_utils import flatten_comments

_RECEIVER_LOG = logger.bind(component="ReceiverPipeline")

ProgressCb = Callable[[str, dict[str, Any]], None] | None


def _emit(cb: ProgressCb, event: str, payload: dict[str, Any]) -> None:
    if cb is None:
        return
    try:
        cb(event, payload)
    except Exception:
        return


def _author_matches(comment: dict[str, Any], sender_user_id: str) -> bool:
    uid = sender_user_id.strip()
    if not uid:
        return False
    author = comment.get("author")
    if isinstance(author, str) and author.strip() == uid:
        return True
    author_id = comment.get("author_id")
    return bool(isinstance(author_id, str) and author_id.strip() == uid)


def locate_sender_stego_comment(post: dict[str, Any], sender_user_id: str) -> dict[str, Any] | None:
    """Pick the sender-authored comment that carries stego text (non-empty body)."""
    matches: list[dict[str, Any]] = []
    for c in flatten_comments(post.get("comments", [])):
        if not _author_matches(c, sender_user_id):
            continue
        body = c.get("body")
        if isinstance(body, str) and body.strip():
            matches.append(c)
    if not matches:
        return None
    if len(matches) > 1:
        _RECEIVER_LOG.warning(
            "receiver_multiple_sender_comments",
            post_id=post.get("id"),
            match_count=len(matches),
        )
    return matches[0]


def _remove_comment_by_id(comments: Any, target_id: str) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(comments, list):
        return [], False
    out: list[dict[str, Any]] = []
    removed = False
    for raw in comments:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id", ""))
        if cid == target_id:
            removed = True
            continue
        replies = raw.get("replies", [])
        new_replies, r = _remove_comment_by_id(replies, target_id)
        if r:
            removed = True
        node = dict(raw)
        node["replies"] = new_replies
        out.append(node)
    return out, removed


def build_pre_sender_post(post: dict[str, Any], sender_comment_id: str) -> dict[str, Any]:
    """Clone post and drop the sender stego comment subtree."""
    clone = dict(post)
    new_comments, ok = _remove_comment_by_id(post.get("comments", []), sender_comment_id)
    if not ok:
        raise ValueError(f"Comment id {sender_comment_id!r} not found in post tree")
    clone["comments"] = new_comments
    return clone


def build_pre_sender_post_all(post: dict[str, Any], sender_user_id: str) -> dict[str, Any]:
    clone = dict(post)
    clone["comments"] = _remove_sender_comments(post.get("comments", []), sender_user_id)
    return clone


def _remove_sender_comments(comments: Any, sender_user_id: str) -> list[dict[str, Any]]:
    if not isinstance(comments, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in comments:
        if not isinstance(raw, dict):
            continue
        if _author_matches(raw, sender_user_id):
            continue
        node = dict(raw)
        node["replies"] = _remove_sender_comments(raw.get("replies", []), sender_user_id)
        out.append(node)
    return out


def nested_angles_from_post(post: dict[str, Any]) -> list[list[dict[str, Any]]]:
    raw = post.get("angles", [])
    if not isinstance(raw, list):
        return []
    return [x if isinstance(x, list) else [x] for x in raw if x is not None]


def _canonicalize_decoded_angle_index(
    decoded_idx: int,
    expected_angle_index: int | None,
    tangents_db: list[dict[str, Any]],
) -> int:
    if expected_angle_index is None or decoded_idx == expected_angle_index:
        return decoded_idx
    if not (0 <= decoded_idx < len(tangents_db)):
        return decoded_idx
    if not (0 <= expected_angle_index < len(tangents_db)):
        return decoded_idx
    if _angle_signature(tangents_db[decoded_idx]) != _angle_signature(
        tangents_db[expected_angle_index]
    ):
        return decoded_idx
    _RECEIVER_LOG.warning(
        "receiver_duplicate_angle_signature_canonicalized",
        decoded_angle_index=decoded_idx,
        expected_angle_index=expected_angle_index,
    )
    return expected_angle_index


def _extract_sender_audit(post: dict[str, Any]) -> SenderAudit | None:
    """Pull the sender's audit trail out of arbitrary post JSON.

    The result is cast to ``SenderAudit``, not proven to match it: this reads whatever a
    (possibly old or foreign) sender artifact actually contains, which is exactly why the
    downstream ``isinstance``/``is not None`` guards on individual fields stay in place even
    though the type declares those fields required.
    """
    explicit = post.get("sender_audit")
    if isinstance(explicit, dict):
        return cast(SenderAudit, explicit)
    embedding = post.get("embedding")
    if not isinstance(embedding, dict):
        return None
    embedded = embedding.get("senderAudit")
    return cast(SenderAudit, embedded) if isinstance(embedded, dict) else None


def _rebuilt_selected_urls_hash(rebuilt_post: dict[str, Any]) -> str:
    urls = rebuilt_post.get("search_results", [])
    if not isinstance(urls, list):
        return stable_hash([])
    return stable_hash([stable_hash(item) for item in urls])


def _context_drift_mismatches(
    sender_audit: SenderAudit, rebuilt_post: dict[str, Any], rebuilt_summary: dict[str, Any]
) -> list[dict[str, Any]]:
    checks = (
        (
            "dictionary_hash",
            sender_audit.get("dictionary_hash"),
            rebuilt_summary.get("dictionary_hash"),
        ),
        (
            "dictionary_count",
            sender_audit.get("dictionary_count"),
            rebuilt_summary.get("dictionary_count"),
        ),
        ("angles_hash", sender_audit.get("angles_hash"), rebuilt_summary.get("angles_hash")),
        ("angles_count", sender_audit.get("angles_count"), rebuilt_summary.get("angles_count")),
        (
            "selected_urls_hash",
            sender_audit.get("selected_urls_hash"),
            _rebuilt_selected_urls_hash(rebuilt_post),
        ),
    )
    return [
        {"field": field, "expected": expected, "actual": actual}
        for field, expected, actual in checks
        # sender_audit is cast, not proven, to match SenderAudit -- see _extract_sender_audit.
        if expected is not None and expected != actual  # pyright: ignore[reportUnnecessaryComparison]
    ]


def _tangent_db_parity_mismatch(
    pre_sender_post: dict[str, Any], gen_angles_report: dict[str, Any]
) -> dict[str, Any] | None:
    """Compare the sender's persisted tangent-DB recipe against the receiver's effective one.

    Warn-only parity check (tangent-db-revamp plan section 5): the persisted post's stored DB
    still governs decoding; a mismatch only diagnoses a config drift between prepare and decode.
    Returns None when the post carries no report or when the config hashes agree.
    """
    sender_report = pre_sender_post.get("tangent_db_report")
    if not isinstance(sender_report, dict):
        return None
    receiver_report = gen_angles_report.get("tangent_db_report")
    receiver_hash = (
        receiver_report.get("config_hash") if isinstance(receiver_report, dict) else None
    )
    sender_hash = sender_report.get("config_hash")
    if sender_hash == receiver_hash:
        return None
    return {
        "sender_config_hash": sender_hash,
        "receiver_config_hash": receiver_hash,
        "sender_config": sender_report.get("config"),
    }


def _payload_transform_from_audit(sender_audit: SenderAudit | None) -> str:
    if not isinstance(sender_audit, dict):
        return get_workflow_payload_transform()
    direct = sender_audit.get("payload_transform")
    if isinstance(direct, str) and direct:
        return direct
    encoding = sender_audit.get("encoding")
    if isinstance(encoding, dict):
        configured = encoding.get("payload_transform")
        if isinstance(configured, str) and configured:
            return configured
    return get_workflow_payload_transform()


def _compressed_full_from_audit(sender_audit: SenderAudit | None) -> str | None:
    if not isinstance(sender_audit, dict):
        return None
    compression = sender_audit.get("compression")
    # sender_audit is cast, not proven, to match SenderAudit -- see _extract_sender_audit.
    if not isinstance(compression, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        return None
    compressed = compression.get("compressed")
    return compressed if isinstance(compressed, str) and compressed else None


def _decode_configured_payload(protected_payload: str, payload_transform: str) -> str:
    payload = unprotect_payload(
        protected_payload,
        transform=payload_transform,
        secret=get_workflow_encoding_secret(),
    )
    if payload is None:
        raise RuntimeError(f"Could not decode payload transform {payload_transform!r}")
    return payload


def _recover_protected_payload_from_bits(bits: str) -> str:
    if not bits.startswith("0"):
        raise RuntimeError("Unsupported multi-frame payload compression mode")
    payload = from_binary_utf8(bits[1:])
    if payload is None:
        raise RuntimeError("Could not decode protected payload bits")
    return payload


def _frame_observation(
    post: dict[str, Any],
    comment: dict[str, Any],
    context: dict[str, Any],
    angles: list[dict[str, Any]],
    decoded_index: int | None,
    post_index: int,
) -> dict[str, Any]:
    base = {
        "post_index": post_index,
        "post_id": post.get("id"),
        "comment_id": comment.get("id"),
        "parent_id": comment.get("parent_id"),
        "created_utc": comment.get("created_utc", 0),
    }
    if decoded_index is None:
        return {**base, "failed": True, "error": "angle_decode_failed"}
    candidates = recoverable_frame_bit_candidates_from_observations(
        post=context,
        parent_id=comment.get("parent_id"),
        decoded_angle_index=decoded_index,
        n_angles=len(angles),
    )
    return {
        **base,
        "decoded_angle_index": decoded_index,
        "frame_candidates": candidates,
        "capacity": len(candidates[0]) if candidates else 0,
        "failed": not bool(candidates),
        "error": None if candidates else "selection_recovery_failed",
    }


def _recover_multi_frame_stream(frames: list[dict[str, Any]]) -> str | None:
    streams = [""]
    for frame in frames:
        next_streams = [
            prefix + candidate
            for prefix in streams
            for candidate in frame.get("frame_candidates", [])
            if _compact_prefix_is_valid(prefix + candidate, len(frames))
        ]
        streams = list(dict.fromkeys(next_streams))[:65536]
        if not streams:
            return None
    valid_streams = [
        stream for stream in streams if parse_multi_frame_stream(stream, len(frames)) is not None
    ]
    payloads = {
        parsed["payload_bits"]
        for stream in valid_streams
        if (parsed := parse_multi_frame_stream(stream, len(frames))) is not None
    }
    return valid_streams[0] if len(payloads) == 1 else None


def _compact_prefix_is_valid(stream: str, expected_frame_count: int) -> bool:
    """Reject candidates once their self-delimiting control prefix is known."""
    count = decode_elias_gamma(stream)
    if count is None:
        return True
    frame_count, offset = count
    if frame_count != expected_frame_count:
        return False
    length = decode_elias_gamma(stream, offset)
    if length is None:
        return True
    payload_bit_length, payload_start = length
    payload_end = payload_start + payload_bit_length
    return len(stream) <= payload_end or set(stream[payload_end:]) <= {"0"}


def _multi_frame_failure(
    frames: list[dict[str, Any]], valid_frames: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "succeeded": False,
        "payload": None,
        "message_id": None,
        "frame_count": len(valid_frames),
        "posts_used": len({frame.get("post_id") for frame in valid_frames}),
        "frames": frames,
        "recovery_meta": {
            "decoded_frames": len(valid_frames),
            "failed_frames": sum(bool(frame.get("failed")) for frame in frames),
            "ordering_source": "caller_supplied_frame_order",
            "protocol": "multi_frame_count_length_v1",
        },
        "error": "Could not reconstruct a valid compact multi-frame payload",
    }


def _multi_frame_success(
    frames: list[dict[str, Any]],
    valid_frames: list[dict[str, Any]],
    protected_payload: str,
    payload: str,
    payload_transform: str,
    framed_bits: str,
) -> dict[str, Any]:
    return {
        "succeeded": True,
        "payload": payload,
        "message_id": stable_hash(protected_payload),
        "frame_count": len(valid_frames),
        "posts_used": len({frame.get("post_id") for frame in valid_frames}),
        "frames": frames,
        "recovery_meta": {
            "decoded_frames": len(valid_frames),
            "failed_frames": sum(bool(frame.get("failed")) for frame in frames),
            "ordering_source": "caller_supplied_frame_order",
            "protocol": "multi_frame_count_length_v1",
            "payload_transform": payload_transform,
            "framed_payload_bits": framed_bits,
        },
    }


class ReceiverPipeline:
    """Orchestrates data-load, research, angles, and decode to recover a stego payload."""

    def __init__(
        self,
        *,
        data_load: DataLoadPipeline | None = None,
        research: ResearchPipeline | None = None,
        gen_angles: GenAnglesPipeline | None = None,
        decode: DecodePipeline | None = None,
    ) -> None:
        self._log = logger.bind(component="ReceiverPipeline")
        self.data_load = data_load or DataLoadPipeline()
        self.research = research or ResearchPipeline()
        self.gen_angles = gen_angles or GenAnglesPipeline()
        self.decode = decode or DecodePipeline()

    def rebuild_context(
        self,
        pre_sender_post: dict[str, Any],
        *,
        selected_parent_id: str | None = None,
        use_fetch_cache: bool = True,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache_research: bool = True,
        allow_fallback: bool = False,
        on_progress: ProgressCb = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Data-load → research → gen-angles on the receiver side."""
        post_id = pre_sender_post.get("id", "<unknown>")
        _emit(
            on_progress,
            "receiver.rebuild_data_load",
            {"post_id": post_id, "tags": ["workflow"]},
        )
        dl = self.data_load.preview_post(dict(pre_sender_post), use_cache=use_fetch_cache)
        post_dl = dl["post"]
        dl_report = dl["report"]
        if not dl_report.get("fetch_success") or not post_dl.get("selftext"):
            raise RuntimeError(
                f"Receiver data-load failed: {dl_report.get('error') or 'no selftext'}"
            )

        _emit(
            on_progress,
            "receiver.rebuild_research",
            {"post_id": post_id, "tags": ["workflow"]},
        )
        rs = self.research.preview_post(
            post_dl,
            force=True,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache_research,
        )
        post_rs = rs["post"]

        _emit(
            on_progress,
            "receiver.regen_angles",
            {"post_id": post_id, "tags": ["workflow"]},
        )
        if get_workflow_context_sampler() == "context_weighted_v2":
            ga = self.gen_angles.preview_post(
                post_rs,
                allow_fallback=allow_fallback,
                selected_parent_id=selected_parent_id,
            )
        else:
            ga = self.gen_angles.preview_post(post_rs, allow_fallback=allow_fallback)
        rebuilt = ga["post"]
        dictionary_report = build_dictionary_report(rebuilt)

        summary = {
            "selftext_hash": stable_hash(rebuilt.get("selftext", "")),
            "selftext_length": len(rebuilt.get("selftext", ""))
            if isinstance(rebuilt.get("selftext"), str)
            else 0,
            "search_results_hash": stable_hash(rebuilt.get("search_results", [])),
            "search_results_count": len(rebuilt.get("search_results", []) or [])
            if isinstance(rebuilt.get("search_results"), list)
            else 0,
            "angles_hash": stable_hash(rebuilt.get("angles", [])),
            "angles_count": len(rebuilt.get("angles", []) or [])
            if isinstance(rebuilt.get("angles"), list)
            else 0,
            "options_count": rebuilt.get("options_count"),
            "dictionary_id": dictionary_report["dictionary_id"],
            "dictionary_hash": dictionary_report["texts_hash"],
            "dictionary_count": dictionary_report["entry_count"],
            "dictionary_raw_count": dictionary_report["raw_entry_count"],
            "dictionary_source_counts": dictionary_report["source_counts"],
            "dictionary_truncated_sources": dictionary_report["truncated_sources"],
            "dictionary_capacity_applied": dictionary_report["capacity_applied"],
        }
        parity_mismatch = _tangent_db_parity_mismatch(pre_sender_post, ga["report"])
        if parity_mismatch is not None:
            self._log.warning("tangent_db_config_mismatch", post_id=post_id, **parity_mismatch)
            summary["tangent_db_config_mismatch"] = parity_mismatch
        reports = {"data_load": dl_report, "research": rs["report"], "gen_angles": ga["report"]}
        return rebuilt, {"summary": summary, "reports": reports}

    def decode_payload(
        self,
        *,
        stego_text: str,
        rebuilt_post: dict[str, Any],
        pre_sender_post: dict[str, Any],
        nested_angles: list[list[dict[str, Any]]],
        compressed_full: str | None = None,
        max_padding_bits: int = 256,
        strict_mode: bool = False,
        expected_angle_index: int | None = None,
        payload_transform: str | None = None,
        on_progress: ProgressCb = None,
    ) -> tuple[str, dict[str, Any]]:
        tangents_db = flatten_nested_angles(rebuilt_post)
        if not tangents_db:
            raise ValueError("Rebuilt post has no angles; cannot decode")
        visible_stego_text = stego_text

        _emit(
            on_progress,
            "receiver.decode_angle",
            {
                "tangents_count": len(tangents_db),
                "stego_preview": text_preview(visible_stego_text),
                "tags": ["workflow"],
            },
        )
        decoded_idx = self.decode.decode(
            stego_text=visible_stego_text,
            angles=tangents_db,
            strict_mode=strict_mode,
        )
        if decoded_idx is None:
            raise RuntimeError("DecodePipeline could not map stego text to an angle index")
        decoded_idx = _canonicalize_decoded_angle_index(
            decoded_idx,
            expected_angle_index,
            tangents_db,
        )
        semantic_decoded_idx = decoded_idx
        authoritative_idx = decoded_idx
        if expected_angle_index is not None and decoded_idx != expected_angle_index:
            _RECEIVER_LOG.warning(
                "receiver_angle_index_mismatch_using_sender_audit",
                expected_angle_index=expected_angle_index,
                decoded_angle_index=decoded_idx,
                strict_mode=strict_mode,
            )
            if strict_mode:
                raise RuntimeError(
                    "Decoded angle index does not match sender audit "
                    f"(expected={expected_angle_index}, got={decoded_idx})"
                )
            authoritative_idx = expected_angle_index

        dictionary = build_dictionary(rebuilt_post)
        _emit(
            on_progress,
            "receiver.decode_payload",
            {"decoded_angle_index": authoritative_idx, "tags": ["workflow"]},
        )

        recovery_meta: dict[str, Any]
        resolved_transform = payload_transform or get_workflow_payload_transform()
        if compressed_full is not None:
            got = recover_payload_with_compressed_full(
                compressed_full,
                dictionary,
                pre_sender_post,
                nested_angles,
                authoritative_idx,
            )
            if got is None:
                raise RuntimeError("Compressed bitstring does not match decoded angle index")
            protected_payload, recovery_meta = got
            recovery_meta["recovery_source"] = "audit_assisted_compressed_full"
            recovery_meta["used_compressed_full"] = True
        else:
            got = recover_payload_bruteforce_comment_bits(
                dictionary,
                pre_sender_post,
                nested_angles,
                authoritative_idx,
                max_padding_bits=max_padding_bits,
            )
            if got is None:
                raise RuntimeError(
                    "Could not recover payload (try optional compressed_bitstring or "
                    "increase max_padding_bits)"
                )
            protected_payload, recovery_meta = got
            recovery_meta["recovery_source"] = "pure_selection_channel"
            recovery_meta["used_compressed_full"] = False

        payload = _decode_configured_payload(protected_payload, resolved_transform)
        recovery_meta["payload_carrier"] = "selection_channel"
        recovery_meta["payload_transform"] = resolved_transform
        recovery_meta["embedded_payload_bytes"] = len(protected_payload.encode("utf-8"))
        recovery_meta["payload_bytes"] = len(payload.encode("utf-8"))
        info = {
            "decoded_angle_index": authoritative_idx,
            "recovery_meta": recovery_meta,
        }
        if semantic_decoded_idx != authoritative_idx:
            info["semantic_decoded_angle_index"] = semantic_decoded_idx
        return payload, info

    def _collect_multi_frame_frames(
        self,
        posts: list[dict[str, Any]],
        sender_user_id: str,
        ordered_frame_refs: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        posts_by_id = {str(post.get("id")): (index, post) for index, post in enumerate(posts)}
        frames: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for ref in ordered_frame_refs:
            post_id, comment_id = str(ref.get("post_id", "")), str(ref.get("comment_id", ""))
            key = (post_id, comment_id)
            if key in seen or post_id not in posts_by_id:
                frames.append(
                    {
                        "post_id": post_id,
                        "comment_id": comment_id,
                        "failed": True,
                        "error": "invalid_frame_reference",
                    }
                )
                continue
            seen.add(key)
            post_index, post = posts_by_id[post_id]
            comment = next(
                (
                    item
                    for item in flatten_comments(post.get("comments", []))
                    if str(item.get("id")) == comment_id
                ),
                None,
            )
            if comment is None or not _author_matches(comment, sender_user_id):
                frames.append(
                    {
                        "post_id": post_id,
                        "comment_id": comment_id,
                        "failed": True,
                        "error": "invalid_frame_reference",
                    }
                )
                continue
            context = build_pre_sender_post_all(post, sender_user_id)
            if get_workflow_context_sampler() == "context_weighted_v2":
                context = self.gen_angles.preview_post(
                    context,
                    selected_parent_id=(
                        str(comment.get("parent_id"))
                        if comment.get("parent_id") is not None
                        else None
                    ),
                )["post"]
            angles = flatten_nested_angles(context)
            body = comment.get("body")
            decoded_index = (
                self.decode.decode(stego_text=body, angles=angles, strict_mode=False)
                if isinstance(body, str) and body.strip()
                else None
            )
            frames.append(
                _frame_observation(post, comment, context, angles, decoded_index, post_index)
            )
        return frames

    def run_multi_frame(
        self,
        posts_or_profile_feed: list[dict[str, Any]],
        sender_user_id: str,
        *,
        ordered_frame_refs: list[dict[str, str]],
        payload_transform: str | None = None,
        on_progress: ProgressCb = None,
    ) -> dict[str, Any]:
        del on_progress
        ordered_frames = self._collect_multi_frame_frames(
            posts_or_profile_feed, sender_user_id, ordered_frame_refs
        )
        valid_frames = [frame for frame in ordered_frames if not frame.get("failed")]
        if len(valid_frames) != len(ordered_frames) or not valid_frames:
            return _multi_frame_failure(ordered_frames, valid_frames)
        framed_bits = _recover_multi_frame_stream(valid_frames)
        resolved_transform = payload_transform or get_workflow_payload_transform()
        if framed_bits is None:
            return _multi_frame_failure(ordered_frames, valid_frames)
        parsed = parse_multi_frame_stream(framed_bits, len(valid_frames))
        if parsed is None:
            return _multi_frame_failure(ordered_frames, valid_frames)
        protected_payload = _recover_protected_payload_from_bits(parsed["payload_bits"])
        payload = _decode_configured_payload(protected_payload, resolved_transform)
        return _multi_frame_success(
            ordered_frames,
            valid_frames,
            protected_payload,
            payload,
            resolved_transform,
            framed_bits,
        )

    def run(
        self,
        post: dict[str, Any],
        sender_user_id: str,
        *,
        use_fetch_cache: bool = True,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache_research: bool = True,
        allow_fallback: bool = False,
        compressed_full: str | None = None,
        max_padding_bits: int = 256,
        fail_on_context_drift: bool = True,
        strict_decode: bool | None = None,
        on_progress: ProgressCb = None,
    ) -> dict[str, Any]:
        post_id = post.get("id", "<unknown>")
        _emit(
            on_progress,
            "receiver.locate_comment",
            {"post_id": post_id, "sender_user_id": sender_user_id, "tags": ["workflow"]},
        )
        located = locate_sender_stego_comment(post, sender_user_id)
        if located is None:
            raise ValueError(
                f"No non-empty comment from sender {sender_user_id!r} found on post {post_id!r}"
            )
        stego_text = str(located.get("body", "")).strip()
        sender_comment_id = str(located.get("id", ""))
        if not sender_comment_id:
            raise ValueError("Located sender comment has no id")
        visible_stego_text = stego_text

        located_summary = {
            "id": located.get("id"),
            "author": located.get("author"),
            "parent_id": located.get("parent_id"),
            "body_preview": text_preview(visible_stego_text),
            "body_hash": stable_hash(stego_text),
            "visible_body_hash": stable_hash(visible_stego_text),
        }

        pre_sender = build_pre_sender_post(post, sender_comment_id)

        rebuilt, rebuild_info = self.rebuild_context(
            pre_sender,
            selected_parent_id=(
                str(located.get("parent_id"))
                if located.get("parent_id") is not None
                else None
            ),
            use_fetch_cache=use_fetch_cache,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache_research=use_fetch_cache_research,
            allow_fallback=allow_fallback,
            on_progress=on_progress,
        )
        sender_audit = _extract_sender_audit(post)
        drift_mismatches: list[dict[str, Any]] = []
        if isinstance(sender_audit, dict):
            drift_mismatches = _context_drift_mismatches(
                sender_audit, rebuilt, rebuild_info["summary"]
            )
        if drift_mismatches and fail_on_context_drift:
            return {
                "succeeded": False,
                "stage": "context_drift",
                "post_id": post.get("id"),
                "located_comment": located_summary,
                "rebuild_summary": rebuild_info["summary"],
                "rebuild_reports": rebuild_info["reports"],
                "context_drift": {
                    "status": "failed",
                    "mismatches": drift_mismatches,
                },
            }

        nested_rebuilt = nested_angles_from_post(rebuilt)
        resolved_strict_decode = (
            get_workflow_decode_strict_default() if strict_decode is None else strict_decode
        )
        payload, decode_info = self.decode_payload(
            stego_text=stego_text,
            rebuilt_post=rebuilt,
            pre_sender_post=pre_sender,
            nested_angles=nested_rebuilt,
            # Audit-provided compressed bits are an assisted recovery path. In
            # ephemeral mode the context is expected to be volatile; pure channel
            # recovery must rely only on the selected comment/angle observations.
            compressed_full=compressed_full or _compressed_full_from_audit(sender_audit),
            max_padding_bits=max_padding_bits,
            strict_mode=resolved_strict_decode,
            expected_angle_index=sender_audit.get("selected_angle_index")
            if isinstance(sender_audit, dict)
            and isinstance(sender_audit.get("selected_angle_index"), int)
            else None,
            payload_transform=_payload_transform_from_audit(sender_audit),
            on_progress=on_progress,
        )

        return {
            "succeeded": True,
            "post_id": post.get("id"),
            "payload": payload,
            "located_comment": located_summary,
            "rebuild_summary": rebuild_info["summary"],
            "decoded_angle_index": decode_info["decoded_angle_index"],
            "recovery_meta": decode_info["recovery_meta"],
            "rebuild_reports": rebuild_info["reports"],
            "context_drift": {
                "status": "checked" if isinstance(sender_audit, dict) else "not_provided",
                "mismatches": drift_mismatches,
            },
        }
