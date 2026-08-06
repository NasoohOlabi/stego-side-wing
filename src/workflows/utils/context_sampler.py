"""Context-conditioned weighted dictionary sampler."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, validate_call

from workflows.utils.comment_context import (
    CommentNode,
    ancestor_chain,
    deduplicate_nodes,
    direct_children,
    index_comment_tree,
    normalize_thing_id,
    normalize_visible_text,
    siblings,
)
from workflows.utils.context_research import RankedResearch, rank_frozen_research
from workflows.utils.protocol_utils import stable_hash, text_preview

CONTEXT_WEIGHTED_SAMPLER_VERSION = "context_weighted_v2"


class ContextSamplerConfig(BaseModel):
    """Immutable effective configuration persisted into dictionary identity."""

    model_config = ConfigDict(frozen=True)
    max_blocks: int = Field(ge=1)
    comment_cap: int = Field(ge=0)
    research_cap: int = Field(ge=0)
    comment_weight: int = Field(default=3, ge=1)
    research_weight: int = Field(default=1, ge=1)
    max_ancestors: int = Field(default=8, ge=0)
    include_children: bool = True
    global_fallback: bool = True


def _rank_nodes(
    nodes: list[CommentNode], post_id: str, parent_id: str | None, tier: str
) -> list[CommentNode]:
    def key(node: CommentNode) -> str:
        return stable_hash(
            {
                "sampler_version": CONTEXT_WEIGHTED_SAMPLER_VERSION,
                "post_id": post_id,
                "selected_parent_id": parent_id,
                "relationship": tier,
                "comment_id": node.comment_id,
                "text": normalize_visible_text(node.text).casefold(),
            }
        )

    return sorted(deduplicate_nodes(nodes), key=key)


def _comment_entry(node: CommentNode, relationship: str) -> dict[str, Any]:
    return {
        "source": "comments",
        "source_id": node.comment_id,
        "comment_id": node.comment_id,
        "relationship": relationship,
        "text": node.text,
    }


def _query_document(
    post: dict[str, Any], selected: CommentNode | None, ancestors: list[CommentNode]
) -> str:
    parts = [post.get("title", ""), post.get("selftext") or post.get("text", "")]
    if selected is not None:
        parts.append(selected.text)
    parts.extend(node.text for node in ancestors)
    return "\n".join(normalize_visible_text(part) for part in parts if part)


def _research_entry(item: RankedResearch) -> dict[str, Any]:
    return {
        "source": "search_results",
        "source_id": item.source_id,
        "url": item.url,
        "text": item.text,
        "research_score": item.score,
        "text_hash": item.text_hash,
    }


def _weighted_fill(
    comments: list[dict[str, Any]],
    research: list[dict[str, Any]],
    slots: int,
    config: ContextSamplerConfig,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    schedule = ["comments"] * config.comment_weight + ["search_results"] * config.research_weight
    queues = {"comments": list(comments), "search_results": list(research)}
    selected: list[dict[str, Any]] = []
    requested: Counter[str] = Counter()
    cursor = 0
    while len(selected) < slots and any(queues.values()):
        source = schedule[cursor % len(schedule)]
        requested[source] += 1
        cursor += 1
        if queues[source]:
            selected.append(queues[source].pop(0))
    return selected, dict(requested)


def _dictionary_report(
    post_id: str,
    parent_id: str | None,
    entries: list[dict[str, Any]],
    config: ContextSamplerConfig,
    research_pool: list[Any],
    raw_relationships: Counter[str],
    requested: dict[str, int],
) -> dict[str, Any]:
    identities = [
        {
            "source": entry["source"],
            "source_id": entry.get("source_id"),
            "relationship": entry.get("relationship"),
            "text_hash": stable_hash(entry["text"]),
        }
        for entry in entries
    ]
    identity = {
        "sampler_version": CONTEXT_WEIGHTED_SAMPLER_VERSION,
        "post_id": post_id,
        "selected_parent_id": parent_id,
        "config": config.model_dump(mode="json"),
        "entries": identities,
    }
    counts = Counter(str(entry["source"]) for entry in entries)
    exhausted = {
        "comments": counts["comments"] < config.comment_cap,
        "search_results": counts["search_results"] < config.research_cap,
    }
    return {
        "dictionary_id": stable_hash(identity),
        "texts_hash": stable_hash([entry["text"] for entry in entries]),
        "sampler_version": CONTEXT_WEIGHTED_SAMPLER_VERSION,
        "selection_strategy": "parent_conditioned_weighted_schedule",
        "selected_parent_id": parent_id,
        "capacity_profile": None,
        "capacity_limits": config.model_dump(mode="json"),
        "capacity_applied": True,
        "truncated_sources": [],
        "raw_entry_count": 1 + sum(raw_relationships.values()) + len(research_pool),
        "entry_count": len(entries),
        "raw_source_counts": {
            "post": 1,
            "comments": sum(raw_relationships.values()),
            "search_results": len(research_pool),
        },
        "source_counts": {
            "post": counts["post"],
            "comments": counts["comments"],
            "search_results": counts["search_results"],
        },
        "selected_source_counts": dict(counts),
        "relationship_counts": dict(raw_relationships),
        "selected_relationship_counts": dict(
            Counter(str(entry.get("relationship")) for entry in entries if entry.get("relationship"))
        ),
        "requested_allocations": requested,
        "effective_allocations": dict(counts),
        "source_exhaustion": exhausted,
        "redistributed_slots": {
            source: max(0, requested.get(source, 0) - counts[source])
            for source in ("comments", "search_results")
        },
        "research_available": bool(research_pool),
        "frozen_research_hash": stable_hash(research_pool),
        "selected_entry_hashes": [item["text_hash"] for item in identities],
        "selected_source_ids": [item["source_id"] for item in identities],
        "sample_entries": [
            {**item, "preview": text_preview(entries[index]["text"], limit=120)}
            for index, item in enumerate(identities[:5])
        ],
    }


@validate_call
def build_context_dictionary_bundle(
    post: dict[str, Any],
    selected_parent_id: str | None,
    config: ContextSamplerConfig,
) -> dict[str, Any]:
    """Build a deterministic parent-conditioned dictionary and full identity report."""
    body = normalize_visible_text(post.get("selftext") or post.get("text", ""))
    if not body:
        raise ValueError("Context-conditioned sampling requires a non-empty post body")
    post_id = normalize_thing_id(post.get("id")) or stable_hash(body)
    parent_id = normalize_thing_id(selected_parent_id)
    index = index_comment_tree(post)
    selected = index.get(parent_id) if parent_id else None
    if parent_id and selected is None and parent_id != post_id:
        raise ValueError(f"Selected parent is missing: {selected_parent_id}")
    if parent_id == post_id:
        parent_id, selected = None, None
    ancestors = ancestor_chain(index, selected.comment_id, post_id) if selected else []
    mandatory_nodes = ([selected] if selected else []) + ancestors[: config.max_ancestors]
    sibling_nodes = siblings(index, selected.comment_id) if selected else direct_children(index, post_id)
    child_nodes = direct_children(index, selected.comment_id) if selected and config.include_children else []
    used_ids = {node.comment_id for node in mandatory_nodes}
    fallback_nodes = [node for node in index.values() if node.comment_id not in used_ids]
    tiers = [
        ("sibling", sibling_nodes),
        ("child", child_nodes),
        ("fallback", fallback_nodes if config.global_fallback else []),
    ]
    optional: list[dict[str, Any]] = []
    selected_texts = {
        normalize_visible_text(node.text).casefold() for node in mandatory_nodes
    }
    for relationship, nodes in tiers:
        for node in _rank_nodes(nodes, post_id, parent_id, relationship):
            normalized_text = normalize_visible_text(node.text).casefold()
            if node.comment_id in used_ids or normalized_text in selected_texts:
                continue
            used_ids.add(node.comment_id)
            selected_texts.add(normalized_text)
            optional.append(_comment_entry(node, relationship))
    mandatory_limit = min(config.comment_cap, max(0, config.max_blocks - 1))
    mandatory = [
        _comment_entry(node, "selected_parent" if node == selected else "ancestor")
        for node in deduplicate_nodes(mandatory_nodes)
    ][:mandatory_limit]
    comment_remaining = max(0, config.comment_cap - len(mandatory))
    optional = optional[:comment_remaining]
    research_pool = post.get("search_results", [])
    research_list = research_pool if isinstance(research_pool, list) else []
    ranked_research = rank_frozen_research(
        research_list, _query_document(post, selected, ancestors)
    )[: config.research_cap]
    slots = max(0, config.max_blocks - 1 - len(mandatory))
    weighted, requested = _weighted_fill(
        optional, [_research_entry(item) for item in ranked_research], slots, config
    )
    entries = [
        {"source": "post", "source_id": post_id, "text": body},
        *mandatory,
        *weighted,
    ]
    raw_relationships = Counter(
        {"selected_parent": int(selected is not None), "ancestor": len(ancestors)}
    )
    raw_relationships.update({"sibling": len(sibling_nodes), "child": len(child_nodes)})
    raw_relationships["fallback"] = len(fallback_nodes)
    report = _dictionary_report(
        post_id, parent_id, entries, config, research_list, raw_relationships, requested
    )
    return {"entries": entries, "texts": [entry["text"] for entry in entries], "report": report}
