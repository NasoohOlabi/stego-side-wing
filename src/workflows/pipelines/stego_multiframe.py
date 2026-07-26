"""Pure planning helpers for multi-frame stego payloads."""

from typing import Any

from workflows.pipelines.stego_comment_tree import planned_parent_id
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
