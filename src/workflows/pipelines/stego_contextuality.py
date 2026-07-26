"""Contextuality/naturalness scoring for a candidate stego text.

Split out of ``stego.py`` (plan step 3.2). ``STOPWORDS`` here is deliberately kept
separate from ``naturalness_gate._STOPWORDS`` (63 vs 33 entries, 32 words only in this
one) -- they drive different decisions (contextuality scoring vs. the naturalness gate)
and were investigated once already; see ``docs/development/refactor-baseline.md``.
"""

import re
from typing import Any

from workflows.contracts import PostAugmentation
from workflows.utils.naturalness_gate import comment_plausibility_gate, naturalness_gate_enabled

STOPWORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "but",
    "by",
    "for",
    "from",
    "get",
    "gets",
    "got",
    "had",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "like",
    "me",
    "more",
    "my",
    "no",
    "not",
    "of",
    "on",
    "or",
    "our",
    "out",
    "she",
    "so",
    "that",
    "the",
    "their",
    "them",
    "there",
    "they",
    "this",
    "to",
    "too",
    "was",
    "we",
    "were",
    "what",
    "when",
    "with",
    "would",
    "you",
    "your",
}
GENERIC_EDITORIAL_PATTERNS = (
    "the bigger issue",
    "the lesson here",
    "at the end of the day",
    "what keeps sticking with me",
    "zoom out",
    "this is really about",
    "wake up call",
)


def tokenize_content_words(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9'-]+", text.lower())
        if len(token) >= 4 and token not in STOPWORDS
    ]


def context_support_texts(
    post_augmentation: PostAugmentation | None,
    sample: dict[str, Any] | None,
    selected_angle: dict[str, Any],
) -> list[str]:
    texts: list[str] = []
    if isinstance(sample, dict):
        for key in ("best_match", "source_quote", "tangent", "category"):
            value = sample.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
    for key in ("source_quote", "tangent", "category"):
        value = selected_angle.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    if isinstance(post_augmentation, dict):
        comment_embedding = post_augmentation.get("commentEmbedding", {})
        context = comment_embedding.get("context", {})
        for key in ("title", "selftext"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
        picked_chain = comment_embedding.get("pickedCommentChain", [])
        if isinstance(picked_chain, list):
            for comment in picked_chain:
                if not isinstance(comment, dict):
                    continue
                body = comment.get("body")
                if isinstance(body, str) and body.strip():
                    texts.append(body)
    return texts


def contextuality_gate(
    text: str,
    *,
    post_augmentation: PostAugmentation | None,
    sample: dict[str, Any] | None,
    selected_angle: dict[str, Any],
) -> dict[str, Any]:
    normalized = " ".join(text.split()).lower()
    support_vocab = set(
        tokenize_content_words(
            " ".join(context_support_texts(post_augmentation, sample, selected_angle))
        )
    )
    candidate_tokens = tokenize_content_words(text)
    overlap = [token for token in candidate_tokens if token in support_vocab]
    unsupported = [token for token in candidate_tokens if token not in support_vocab]
    generic_patterns = [pattern for pattern in GENERIC_EDITORIAL_PATTERNS if pattern in normalized]
    has_supported_phrase = False
    for value in (
        selected_angle.get("source_quote"),
        selected_angle.get("tangent"),
        sample.get("best_match") if isinstance(sample, dict) else None,
    ):
        if isinstance(value, str):
            phrase_tokens = tokenize_content_words(value)
            if phrase_tokens and any(token in set(overlap) for token in phrase_tokens):
                has_supported_phrase = True
                break
    reasons: list[str] = []
    if generic_patterns:
        reasons.append("generic_editorial_tone")
    if not overlap:
        reasons.append("no_context_overlap")
    if len(unsupported) >= 5 and len(overlap) < 2:
        reasons.append("unsupported_topic_drift")
    if not has_supported_phrase and len(overlap) < 2:
        reasons.append("weak_selected_angle_grounding")
    plausibility = {"enabled": False, "passes": True, "reasons": []}
    if naturalness_gate_enabled():
        plausibility = {"enabled": True, **comment_plausibility_gate(text)}
        plausibility_reasons = plausibility.get("reasons", [])
        if isinstance(plausibility_reasons, list):
            reasons.extend(str(reason) for reason in plausibility_reasons)
    return {
        "passes": not reasons,
        "overlap_tokens": overlap[:12],
        "unsupported_tokens": unsupported[:12],
        "generic_patterns": generic_patterns,
        "plausibility": plausibility,
        "reasons": reasons,
    }
