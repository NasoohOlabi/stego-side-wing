"""Deterministic, parent-aware helpers for a nested comment tree."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, validate_call

Relationship = Literal["selected_parent", "ancestor", "sibling", "child", "fallback"]


class CommentNode(BaseModel):
    """Immutable normalized view of one visible comment."""

    model_config = ConfigDict(frozen=True)
    comment_id: str
    parent_id: str | None
    text: str


def normalize_visible_text(value: Any) -> str:
    """Normalize visible text for identity and duplicate detection."""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", text).strip()


def normalize_thing_id(value: Any) -> str | None:
    """Normalize Reddit-style ``t1_``/``t3_`` IDs without guessing missing IDs."""
    if value is None:
        return None
    text = normalize_visible_text(value)
    if not text:
        return None
    return text.split("_", 1)[1] if text.startswith(("t1_", "t3_")) else text


def _walk_comments(
    comments: Any, inherited_parent: str | None, output: list[CommentNode]
) -> None:
    if not isinstance(comments, list):
        return
    for raw in comments:
        if not isinstance(raw, dict):
            continue
        comment_id = normalize_thing_id(raw.get("id"))
        text = normalize_visible_text(raw.get("body", ""))
        if comment_id is None:
            raise ValueError("Every visible comment requires a non-empty id")
        explicit_parent = normalize_thing_id(raw.get("parent_id"))
        output.append(
            CommentNode(
                comment_id=comment_id,
                parent_id=explicit_parent or inherited_parent,
                text=text,
            )
        )
        _walk_comments(raw.get("replies"), comment_id, output)


@validate_call
def index_comment_tree(post: dict[str, Any]) -> dict[str, CommentNode]:
    """Index comments and reject duplicate IDs rather than selecting arbitrarily."""
    nodes: list[CommentNode] = []
    _walk_comments(post.get("comments"), normalize_thing_id(post.get("id")), nodes)
    index: dict[str, CommentNode] = {}
    for node in nodes:
        if node.comment_id in index:
            raise ValueError(f"Duplicate comment id: {node.comment_id}")
        index[node.comment_id] = node
    return index


def ancestor_chain(
    index: dict[str, CommentNode], selected_parent_id: str, post_id: str | None
) -> list[CommentNode]:
    """Return nearest-first comment ancestors, excluding the selected parent."""
    ancestors: list[CommentNode] = []
    seen = {selected_parent_id}
    current = index[selected_parent_id]
    while current.parent_id and current.parent_id != post_id:
        if current.parent_id in seen or current.parent_id not in index:
            raise ValueError(f"Malformed parent chain at {current.parent_id}")
        current = index[current.parent_id]
        seen.add(current.comment_id)
        ancestors.append(current)
    return ancestors


def siblings(
    index: dict[str, CommentNode], selected_parent_id: str
) -> list[CommentNode]:
    """Return immediate siblings of a selected comment."""
    selected = index[selected_parent_id]
    return [
        node
        for node in index.values()
        if node.parent_id == selected.parent_id and node.comment_id != selected_parent_id
    ]


def direct_children(
    index: dict[str, CommentNode], parent_id: str
) -> list[CommentNode]:
    """Return direct children of a comment or normalized post root."""
    return [node for node in index.values() if node.parent_id == parent_id]


def deduplicate_nodes(nodes: list[CommentNode]) -> list[CommentNode]:
    """Deduplicate by ID and normalized visible text while preserving order."""
    output: list[CommentNode] = []
    ids: set[str] = set()
    texts: set[str] = set()
    for node in nodes:
        normalized = normalize_visible_text(node.text).casefold()
        if node.comment_id in ids or not normalized or normalized in texts:
            continue
        ids.add(node.comment_id)
        texts.add(normalized)
        output.append(node)
    return output
