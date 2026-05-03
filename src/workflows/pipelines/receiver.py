"""Receiver pipeline: rebuild post context and recover stego payload."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loguru import logger

from infrastructure.config import (
    get_workflow_decode_strict_default,
    get_workflow_encoding_secret,
    get_workflow_payload_transform,
)
from workflows.pipelines.data_load import DataLoadPipeline
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.research import ResearchPipeline
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.stego_codec import (
    build_dictionary,
    build_dictionary_report,
    extract_invisible_payload,
    flatten_nested_angles,
    recover_payload_bruteforce_comment_bits,
    recover_payload_with_compressed_full,
    strip_invisible_payload,
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
    if isinstance(author_id, str) and author_id.strip() == uid:
        return True
    return False


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


def nested_angles_from_post(post: dict[str, Any]) -> list[list[dict[str, Any]]]:
    raw = post.get("angles", [])
    if not isinstance(raw, list):
        return []
    return [x if isinstance(x, list) else [x] for x in raw if x is not None]


def _angle_signature(angle: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(angle.get("category", "")),
        str(angle.get("source_quote", "")),
        str(angle.get("tangent", "")),
    )


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


def _extract_sender_audit(post: dict[str, Any]) -> dict[str, Any] | None:
    explicit = post.get("sender_audit")
    if isinstance(explicit, dict):
        return explicit
    embedding = post.get("embedding")
    if not isinstance(embedding, dict):
        return None
    embedded = embedding.get("senderAudit")
    return embedded if isinstance(embedded, dict) else None


def _rebuilt_selected_urls_hash(rebuilt_post: dict[str, Any]) -> str:
    urls = rebuilt_post.get("search_results", [])
    if not isinstance(urls, list):
        return stable_hash([])
    return stable_hash([stable_hash(item) for item in urls])


def _context_drift_mismatches(
    sender_audit: dict[str, Any], rebuilt_post: dict[str, Any], rebuilt_summary: dict[str, Any]
) -> list[dict[str, Any]]:
    checks = (
        ("dictionary_hash", sender_audit.get("dictionary_hash"), rebuilt_summary.get("dictionary_hash")),
        ("dictionary_count", sender_audit.get("dictionary_count"), rebuilt_summary.get("dictionary_count")),
        ("angles_hash", sender_audit.get("angles_hash"), rebuilt_summary.get("angles_hash")),
        ("angles_count", sender_audit.get("angles_count"), rebuilt_summary.get("angles_count")),
        ("selected_urls_hash", sender_audit.get("selected_urls_hash"), _rebuilt_selected_urls_hash(rebuilt_post)),
    )
    return [
        {"field": field, "expected": expected, "actual": actual}
        for field, expected, actual in checks
        if expected is not None and expected != actual
    ]


def _payload_transform_from_audit(sender_audit: dict[str, Any] | None) -> str:
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


def _decode_configured_payload(protected_payload: str, payload_transform: str) -> str:
    payload = unprotect_payload(
        protected_payload,
        transform=payload_transform,
        secret=get_workflow_encoding_secret(),
    )
    if payload is None:
        raise RuntimeError(f"Could not decode payload transform {payload_transform!r}")
    return payload


class ReceiverPipeline:
    """Orchestrates data-load, research, angles, and decode to recover a stego payload."""

    def __init__(self) -> None:
        self._log = logger.bind(component="ReceiverPipeline")
        self.data_load = DataLoadPipeline()
        self.research = ResearchPipeline()
        self.gen_angles = GenAnglesPipeline()
        self.decode = DecodePipeline()

    def rebuild_context(
        self,
        pre_sender_post: dict[str, Any],
        *,
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
        visible_stego_text = strip_invisible_payload(stego_text)
        hidden_payload = extract_invisible_payload(stego_text)

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
        if hidden_payload is not None:
            payload = _decode_configured_payload(hidden_payload, resolved_transform)
            recovery_meta = {
                "payload_carrier": "invisible_suffix_utf8",
                "payload_transform": resolved_transform,
                "payload_bytes": len(payload.encode("utf-8")),
                "embedded_payload_bytes": len(hidden_payload.encode("utf-8")),
            }
            if compressed_full is not None:
                got = recover_payload_with_compressed_full(
                    compressed_full,
                    dictionary,
                    pre_sender_post,
                    nested_angles,
                    authoritative_idx,
                )
                if got is None:
                    raise RuntimeError(
                        "Visible selection channel does not match the decoded angle index."
                    )
                visible_payload, visible_meta = got
                if visible_payload != payload:
                    raise RuntimeError(
                        "Invisible payload does not match the visible selection channel."
                    )
                recovery_meta["visible_channel_verified"] = True
                recovery_meta["visible_comment_bits"] = visible_meta.get("comment_bits")
                recovery_meta["visible_angle_bits"] = visible_meta.get("angle_bits")
                recovery_meta["selection_signature"] = (
                    f"{visible_meta.get('comment_bits', '')}{visible_meta.get('angle_bits', '')}"
                )
            else:
                recovery_meta["visible_channel_verified"] = False
            info = {
                "decoded_angle_index": authoritative_idx,
                "recovery_meta": recovery_meta,
            }
            if semantic_decoded_idx != authoritative_idx:
                info["semantic_decoded_angle_index"] = semantic_decoded_idx
            return payload, info
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

        payload = _decode_configured_payload(protected_payload, resolved_transform)
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
        visible_stego_text = strip_invisible_payload(stego_text)

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
            compressed_full=compressed_full,
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
