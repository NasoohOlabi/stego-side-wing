"""Extractive (non-LLM) stego text fallback: reuse thread text verbatim.

Split out of ``stego.py`` (plan step 3.2). Feeds only ``StegoPipeline._encode_extractive_zero_kld``.
"""

from typing import Any

from workflows.utils.stego_codec import flatten_comments


def extractive_candidate_texts(post: dict[str, Any]) -> list[str]:
    bodies = []
    for comment in flatten_comments(post.get("comments", [])):
        body = comment.get("body")
        if isinstance(body, str) and body.strip():
            bodies.append(body.strip())
    selftext = post.get("selftext", "")
    if isinstance(selftext, str) and selftext.strip():
        bodies.append(selftext.strip())
    return bodies


def extractive_angle_matches(candidate_text: str, selected_angle: dict[str, Any]) -> bool:
    for key in ("source_quote", "tangent"):
        value = selected_angle.get(key)
        if isinstance(value, str) and value.strip() and value.strip() in candidate_text:
            return True
    return False


def extractive_stego_text(post: dict[str, Any], selected_angle: dict[str, Any]) -> str:
    candidates = extractive_candidate_texts(post)
    for candidate in candidates:
        if extractive_angle_matches(candidate, selected_angle):
            return "\n".join(candidates)
    return ""
