"""Pure planning helpers for multi-frame stego payloads."""

from collections.abc import Callable
from typing import Any

from workflows.pipelines.stego_comment_tree import planned_parent_id
from workflows.utils.angle_artifact import assert_single_sampler_lane
from workflows.utils.protocol_utils import stable_hash
from workflows.utils.stego_codec import (
    augment_post_with_recoverable_selection_bits,
    build_multi_frame_stream,
    comment_selection_index,
    selection_channel_capacity_report,
)


def multi_frame_slots(
    posts: list[dict[str, Any]], max_frames_per_post: int
) -> list[dict[str, Any]]:
    if max_frames_per_post <= 0:
        return []
    slots: list[dict[str, Any]] = []
    for post_index, post in enumerate(posts):
        capacity_report = selection_channel_capacity_report(post)
        capacity = int(capacity_report["recoverable_capacity_bits"])
        if capacity > 0:
            slots.extend(
                {
                    "post": post,
                    "post_index": post_index,
                    "capacity": capacity,
                    "capacity_report": capacity_report,
                }
                for _ in range(max_frames_per_post)
            )
    return slots


def planned_frame(
    slot: dict[str, Any], frame_index: int, stream_bits: str, cursor: int
) -> dict[str, Any]:
    post = slot["post"]
    capacity = slot["capacity"]
    frame_end = min(cursor + capacity, len(stream_bits))
    augmentation = augment_post_with_recoverable_selection_bits(stream_bits[cursor:frame_end], post)
    parent_id = planned_parent_id(augmentation)
    return {
        "post_index": slot["post_index"],
        "frame_index": frame_index,
        "frame_bits": stream_bits[cursor:frame_end],
        "bit_start": cursor,
        "bit_end": frame_end,
        "capacity": capacity,
        "capacity_report": slot["capacity_report"],
        "bits_used": frame_end - cursor,
        "padding_bits": max(0, capacity - (frame_end - cursor)),
        "post_id": post.get("id"),
        "parent_id": parent_id,
        "comment_selection_index": comment_selection_index(post, parent_id),
        "selected_angle_index": augmentation.get("angleEmbedding", {})
        .get("selectedAngle", {})
        .get("idx"),
        "selection_bits": {
            "comment_bits": augmentation.get("commentBits", ""),
            "angle_bits": augmentation.get("angleBits", ""),
        },
        "embedding_plan": augmentation,
    }


def plan_payload_frames(
    payload: str, prepared: dict[str, Any], posts: list[dict[str, Any]], max_frames_per_post: int
) -> dict[str, Any]:
    assert_single_sampler_lane(posts)
    payload_bits = prepared["payload_bits"]
    slots = multi_frame_slots(posts, max_frames_per_post)
    available_capacity = sum(int(slot["capacity"]) for slot in slots)
    selected = next(
        (
            (candidate, count)
            for count in range(1, len(slots) + 1)
            if (candidate := build_multi_frame_stream(payload_bits, count))
            and sum(int(slot["capacity"]) for slot in slots[:count])
            >= len(candidate["stream_bits"])
        ),
        None,
    )
    if selected is None:
        return {
            "succeeded": False,
            "payload": payload,
            "message_id": stable_hash(prepared["protected_payload"]),
            "frame_count": 0,
            "posts_used": 0,
            "frames": [],
            "prepared_payload": prepared,
            "remaining_bits": payload_bits,
            "available_capacity_bits": available_capacity,
            "required_capacity_bits": len(
                build_multi_frame_stream(payload_bits, max(1, len(slots)))["stream_bits"]
            ),
            "error": "Insufficient multi-frame capacity for compact payload stream",
        }
    stream, count = selected
    cursor = 0
    frames = []
    for index, slot in enumerate(slots[:count]):
        frames.append(planned_frame(slot, index, stream["stream_bits"], cursor))
        cursor += int(slot["capacity"])
    return {
        "succeeded": True,
        "payload": payload,
        "message_id": stable_hash(prepared["protected_payload"]),
        "frame_count": len(frames),
        "posts_used": len({frame["post_id"] for frame in frames}),
        "frames": frames,
        "recovery_meta": {
            "payload_transform": prepared["payload_transform"],
            "protocol": "multi_frame_count_length_v1",
            "ordering_source": "sender_frame_order",
            "stream_bit_length": len(stream["stream_bits"]),
            "control_bit_length": len(stream["control_bits"]),
            "payload_bit_length": len(payload_bits),
            "available_capacity_bits": available_capacity,
        },
        "prepared_payload": prepared,
        "remaining_bits": "",
        "error": None,
    }


ContextPostResolver = Callable[[dict[str, Any], str | None], dict[str, Any]]


def _parent_for_stream_bits(post: dict[str, Any], bits: str) -> str | None:
    """Resolve the parent channel before a parent-conditioned tangent DB exists."""
    probe = dict(post)
    if not probe.get("angles"):
        probe["angles"] = [{"tangent": "capacity probe"}]
    augmentation = augment_post_with_recoverable_selection_bits(bits, probe)
    return planned_parent_id(augmentation)


def _contextual_frames_for_stream(
    stream_bits: str,
    slots: list[dict[str, Any]],
    count: int,
    resolver: ContextPostResolver,
) -> tuple[list[dict[str, Any]], int]:
    frames: list[dict[str, Any]] = []
    cursor = 0
    for index, slot in enumerate(slots[:count]):
        base_report = selection_channel_capacity_report(slot["post"])
        parent_width = int(base_report["comment_recoverable_bits"])
        parent_bits = stream_bits[cursor : cursor + parent_width]
        parent_id = _parent_for_stream_bits(slot["post"], parent_bits)
        resolved_post = resolver(slot["post"], parent_id)
        resolved_report = selection_channel_capacity_report(resolved_post)
        capacity = int(resolved_report["recoverable_capacity_bits"])
        contextual_slot = {
            **slot,
            "post": resolved_post,
            "capacity": capacity,
            "capacity_report": resolved_report,
        }
        frame = planned_frame(contextual_slot, index, stream_bits, cursor)
        frame["parent_recoverable_width"] = parent_width
        frame["tangent_recoverable_width"] = int(
            resolved_report["tangent_recoverable_bits"]
        )
        frame["context_dictionary_id"] = (
            resolved_post.get("angle_artifact", {}).get("dictionary_id")
        )
        frame["tangent_hash"] = resolved_post.get("angle_artifact", {}).get(
            "tangent_hash"
        )
        frame["_context_post"] = resolved_post
        frames.append(frame)
        cursor += capacity
    return frames, cursor


def plan_payload_frames_contextual(
    payload: str,
    prepared: dict[str, Any],
    posts: list[dict[str, Any]],
    max_frames_per_post: int,
    resolver: ContextPostResolver,
) -> dict[str, Any]:
    """Plan frames in two stages: parent bits first, then verified tangent width."""
    assert_single_sampler_lane(posts)
    slots = [
        {"post": post, "post_index": post_index}
        for post_index, post in enumerate(posts)
        for _ in range(max(0, max_frames_per_post))
        if int(selection_channel_capacity_report(post)["comment_choices"]) > 0
    ]
    payload_bits = prepared["payload_bits"]
    selected: tuple[dict[str, Any], list[dict[str, Any]], int] | None = None
    for count in range(1, len(slots) + 1):
        stream = build_multi_frame_stream(payload_bits, count)
        frames, consumed = _contextual_frames_for_stream(
            stream["stream_bits"], slots, count, resolver
        )
        if consumed >= len(stream["stream_bits"]):
            selected = stream, frames, consumed
            break
    if selected is None:
        return {
            "succeeded": False,
            "payload": payload,
            "message_id": stable_hash(prepared["protected_payload"]),
            "frame_count": 0,
            "frames": [],
            "prepared_payload": prepared,
            "remaining_bits": payload_bits,
            "error": "Insufficient parent-conditioned multi-frame capacity",
        }
    stream, frames, available = selected
    return {
        "succeeded": True,
        "payload": payload,
        "message_id": stable_hash(prepared["protected_payload"]),
        "frame_count": len(frames),
        "posts_used": len({frame["post_id"] for frame in frames}),
        "frames": frames,
        "recovery_meta": {
            "payload_transform": prepared["payload_transform"],
            "protocol": "multi_frame_count_length_v1",
            "ordering_source": "sender_frame_order",
            "stream_bit_length": len(stream["stream_bits"]),
            "control_bit_length": len(stream["control_bits"]),
            "payload_bit_length": len(payload_bits),
            "available_capacity_bits": available,
            "sampler_version": "context_weighted_v2",
        },
        "prepared_payload": prepared,
        "remaining_bits": "",
        "error": None,
    }
