"""Pure comment-tree helpers used while assembling a stego encode artifact.

Split out of ``stego.py`` (plan step 3.2): none of these depend on ``StegoPipeline``
state, an LLM, or the backend -- they only shape/read the ``comments`` tree and the
``post_augmentation`` selection-embedding result.
"""

import json
from typing import Any

from workflows.contracts import PostAugmentation


def clone_post(post: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(post))


def append_comment_to_tree(
    comments: list[dict[str, Any]],
    comment: dict[str, Any],
    parent_id: str | None,
) -> list[dict[str, Any]]:
    updated, _ = append_comment_to_tree_with_flag(comments, comment, parent_id)
    return updated


def append_comment_to_tree_with_flag(
    comments: list[dict[str, Any]],
    comment: dict[str, Any],
    parent_id: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    if not parent_id:
        return [*comments, comment], True
    out: list[dict[str, Any]] = []
    inserted = False
    for raw in comments:
        node = dict(raw)
        replies = list(node.get("replies", []) or [])
        current_id = str(node.get("id", ""))
        if current_id == parent_id or current_id.split("_", 1)[-1] == parent_id:
            node["replies"] = [*replies, comment]
            inserted = True
        else:
            node["replies"], child_inserted = append_comment_to_tree_with_flag(
                replies, comment, parent_id
            )
            inserted = inserted or child_inserted
        out.append(node)
    return (out, True) if inserted else (list(comments), False)


def planned_parent_id(post_augmentation: PostAugmentation) -> str | None:
    chain = post_augmentation.get("commentEmbedding", {}).get("pickedCommentChain", [])
    if not isinstance(chain, list) or not chain:
        return None
    parent_id = chain[-1].get("id")
    return str(parent_id) if isinstance(parent_id, str) and parent_id else None
