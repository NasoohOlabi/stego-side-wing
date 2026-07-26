"""Deterministic decode-assistance anchor text for a selected angle.

Split out of ``stego.py`` (plan step 3.2). Used by ``StegoPipeline._revise_candidate_text_contextually``
when a candidate needs a stronger, decodable anchor to the selected angle.
"""

import re
from typing import Any

from workflows.pipelines.stego_contextuality import tokenize_content_words


def clean_angle_anchor_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text:
        return ""
    json_values = re.findall(
        r'"(?:title|summary|source_quote|tangent)"\s*:\s*"([^"]+)"',
        text,
    )
    if json_values:
        titles = re.findall(r'"title"\s*:\s*"([^"]+)"', text)
        text = titles[0] if titles else max(json_values, key=len)
    text = re.sub(r"[\[\]{}]", " ", text)
    text = re.sub(r'"\s*,?\s*"(?:summary|title|error|key_points)"\s*:\s*', " ", text)
    text = " ".join(text.strip(" ,.:;\"'").split())
    if len(text) > 180:
        text = text[:180].rsplit(" ", 1)[0].strip()
    return text


def selected_angle_anchor_phrase(selected_angle: dict[str, Any]) -> str:
    source_quote = clean_angle_anchor_text(selected_angle.get("source_quote"))
    if len(tokenize_content_words(source_quote)) >= 2:
        return source_quote
    tangent = clean_angle_anchor_text(selected_angle.get("tangent"))
    if len(tokenize_content_words(tangent)) >= 2:
        return tangent
    return ""


def with_selected_angle_anchor_variants(
    texts: list[str],
    selected_angle: dict[str, Any],
) -> list[str]:
    phrase = selected_angle_anchor_phrase(selected_angle)
    if not phrase:
        return texts
    phrase_tokens = set(tokenize_content_words(phrase))
    if not phrase_tokens:
        return texts
    for text in texts:
        if len(phrase_tokens & set(tokenize_content_words(text))) >= min(3, len(phrase_tokens)):
            return texts
    if len(phrase.split()) <= 8:
        anchored = f"I can see why people keep coming back to {phrase}."
    else:
        anchored = f"{phrase}. I can see why people keep coming back to that point."
    anchored = " ".join(anchored.split())
    if len(anchored) > 260:
        anchored = anchored[:260].rsplit(" ", 1)[0].strip()
    if anchored and anchored not in texts:
        return [*texts, anchored]
    return texts


def is_synthetic_anchor_text(text: str) -> bool:
    """Identify the deterministic decode-assistance fallback, not a model reply."""
    normalized = " ".join(text.split()).lower()
    return "i can see why people keep coming back to" in normalized
