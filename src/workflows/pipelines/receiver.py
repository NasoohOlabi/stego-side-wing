"""Receiver pipeline: rebuild post context and recover stego payload."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from workflows.pipelines.data_load import DataLoadPipeline
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.research import ResearchPipeline
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.stego_codec import (
    build_dictionary,
    flatten_nested_angles,
    recover_payload_bruteforce_comment_bits,
    recover_payload_with_compressed_full,
)
from workflows.utils.text_utils import flatten_comments

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str, Dict[str, Any]], None]]


def _emit(cb: ProgressCb, event: str, payload: Dict[str, Any]) -> None:
    if cb is None:
        return
    try:
        cb(event, payload)
    except Exception:
        return


def _author_matches(comment: Dict[str, Any], sender_user_id: str) -> bool:
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


def locate_sender_stego_comment(
    post: Dict[str, Any], sender_user_id: str
) -> Optional[Dict[str, Any]]:
    """Pick the sender-authored comment that carries stego text (non-empty body)."""
    matches: List[Dict[str, Any]] = []
    for c in flatten_comments(post.get("comments", [])):
        if not _author_matches(c, sender_user_id):
            continue
        body = c.get("body")
        if isinstance(body, str) and body.strip():
            matches.append(c)
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(
            "receiver multiple sender comments post_id=%s count=%s using first",
            post.get("id"),
            len(matches),
        )
    return matches[0]


def _remove_comment_by_id(comments: Any, target_id: str) -> Tuple[List[Dict[str, Any]], bool]:
    if not isinstance(comments, list):
        return [], False
    out: List[Dict[str, Any]] = []
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


def build_pre_sender_post(post: Dict[str, Any], sender_comment_id: str) -> Dict[str, Any]:
    """Clone post and drop the sender stego comment subtree."""
    clone = dict(post)
    new_comments, ok = _remove_comment_by_id(post.get("comments", []), sender_comment_id)
    if not ok:
        raise ValueError(f"Comment id {sender_comment_id!r} not found in post tree")
    clone["comments"] = new_comments
    return clone


def nested_angles_from_post(post: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    raw = post.get("angles", [])
    if not isinstance(raw, list):
        return []
    return [x if isinstance(x, list) else [x] for x in raw if x is not None]


class ReceiverPipeline:
    """Decode stego payload from a post + agreed sender user id."""

    def __init__(self) -> None:
        self.data_load = DataLoadPipeline()
        self.research = ResearchPipeline()
        self.gen_angles = GenAnglesPipeline()
        self.decode = DecodePipeline()

    def rebuild_context(
        self,
        pre_sender_post: Dict[str, Any],
        *,
        use_fetch_cache: bool = True,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache_research: bool = True,
        allow_fallback: bool = False,
        on_progress: ProgressCb = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
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
        }
        reports = {"data_load": dl_report, "research": rs["report"], "gen_angles": ga["report"]}
        return rebuilt, {"summary": summary, "reports": reports}

    def decode_payload(
        self,
        *,
        stego_text: str,
        rebuilt_post: Dict[str, Any],
        pre_sender_post: Dict[str, Any],
        nested_angles: List[List[Dict[str, Any]]],
        compressed_full: Optional[str] = None,
        max_padding_bits: int = 256,
        on_progress: ProgressCb = None,
    ) -> Tuple[str, Dict[str, Any]]:
        tangents_db = flatten_nested_angles(rebuilt_post)
        if not tangents_db:
            raise ValueError("Rebuilt post has no angles; cannot decode")

        _emit(
            on_progress,
            "receiver.decode_angle",
            {
                "tangents_count": len(tangents_db),
                "stego_preview": text_preview(stego_text),
                "tags": ["workflow"],
            },
        )
        decoded_idx = self.decode.decode(stego_text=stego_text, angles=tangents_db)
        if decoded_idx is None:
            raise RuntimeError("DecodePipeline could not map stego text to an angle index")

        dictionary = build_dictionary(rebuilt_post)
        _emit(
            on_progress,
            "receiver.decode_payload",
            {"decoded_angle_index": decoded_idx, "tags": ["workflow"]},
        )

        recovery_meta: Dict[str, Any]
        if compressed_full is not None:
            got = recover_payload_with_compressed_full(
                compressed_full,
                dictionary,
                pre_sender_post,
                nested_angles,
                decoded_idx,
            )
            if got is None:
                raise RuntimeError("Compressed bitstring does not match decoded angle index")
            payload, recovery_meta = got
        else:
            got = recover_payload_bruteforce_comment_bits(
                dictionary,
                pre_sender_post,
                nested_angles,
                decoded_idx,
                max_padding_bits=max_padding_bits,
            )
            if got is None:
                raise RuntimeError(
                    "Could not recover payload (try optional compressed_bitstring or "
                    "increase max_padding_bits)"
                )
            payload, recovery_meta = got

        return payload, {"decoded_angle_index": decoded_idx, "recovery_meta": recovery_meta}

    def run(
        self,
        post: Dict[str, Any],
        sender_user_id: str,
        *,
        use_fetch_cache: bool = True,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache_research: bool = True,
        allow_fallback: bool = False,
        compressed_full: Optional[str] = None,
        max_padding_bits: int = 256,
        on_progress: ProgressCb = None,
    ) -> Dict[str, Any]:
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

        located_summary = {
            "id": located.get("id"),
            "author": located.get("author"),
            "parent_id": located.get("parent_id"),
            "body_preview": text_preview(stego_text),
            "body_hash": stable_hash(stego_text),
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

        nested_rebuilt = nested_angles_from_post(rebuilt)
        payload, decode_info = self.decode_payload(
            stego_text=stego_text,
            rebuilt_post=rebuilt,
            pre_sender_post=pre_sender,
            nested_angles=nested_rebuilt,
            compressed_full=compressed_full,
            max_padding_bits=max_padding_bits,
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
        }
