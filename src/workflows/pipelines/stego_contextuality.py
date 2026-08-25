"""Contextuality/naturalness scoring for a candidate stego text.

Split out of ``stego.py`` (plan step 3.2). ``STOPWORDS`` here is deliberately kept
separate from ``naturalness_gate._STOPWORDS`` (63 vs 33 entries, 32 words only in this
one) -- they drive different decisions (contextuality scoring vs. the naturalness gate)
and were investigated once already; see ``docs/development/refactor-baseline.md``.
"""

import re
from typing import Any

from infrastructure.config import get_workflow_barb_stance_gate
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
SAFE_UNIVERSAL_PATTERNS = (
    "we could all just",
    "wouldn't it be nice",
    "live in the same country",
    "i think it would be great if we all",
    "if we all just",
    "why can't we all",
    "we should all just",
)
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?]\s+)([A-Z][a-z]{2,})\b")


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


def _post_title_selftext(post_augmentation: PostAugmentation | None) -> str:
    if not isinstance(post_augmentation, dict):
        return ""
    context = post_augmentation.get("commentEmbedding", {}).get("context", {})
    if not isinstance(context, dict):
        return ""
    parts: list[str] = []
    for key in ("title", "selftext"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return " ".join(parts)


def _has_proper_nounish_cues(text: str) -> bool:
    """True for numerals, acronyms, or Capitalized words that are not sentence-initial."""
    if re.search(r"\d", text) or re.search(r"\b[A-Z]{2,}\b", text):
        return True
    sentence_starts = {match.group(1) for match in _SENTENCE_START_RE.finditer(text)}
    for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
        if match.group(0) not in sentence_starts:
            return True
    return False


def _barb_stance_reasons(
    text: str,
    *,
    normalized: str,
    candidate_tokens: list[str],
    post_augmentation: PostAugmentation | None,
) -> list[str]:
    if not get_workflow_barb_stance_gate():
        return []
    reasons: list[str] = []
    safe_hits = [pattern for pattern in SAFE_UNIVERSAL_PATTERNS if pattern in normalized]
    if safe_hits:
        reasons.append("safe_universal_sentiment")
    title_vocab = set(tokenize_content_words(_post_title_selftext(post_augmentation)))
    title_overlap = [token for token in candidate_tokens if token in title_vocab]
    if not title_overlap and not _has_proper_nounish_cues(text):
        reasons.append("weak_thread_specificity")
    return reasons


def _has_supported_angle_phrase(
    *,
    overlap: list[str],
    sample: dict[str, Any] | None,
    selected_angle: dict[str, Any],
) -> bool:
    overlap_set = set(overlap)
    for value in (
        selected_angle.get("source_quote"),
        selected_angle.get("tangent"),
        sample.get("best_match") if isinstance(sample, dict) else None,
    ):
        if not isinstance(value, str):
            continue
        phrase_tokens = tokenize_content_words(value)
        if phrase_tokens and any(token in overlap_set for token in phrase_tokens):
            return True
    return False


def _base_contextuality_reasons(
    *,
    overlap: list[str],
    unsupported: list[str],
    generic_patterns: list[str],
    has_supported_phrase: bool,
) -> list[str]:
    reasons: list[str] = []
    if generic_patterns:
        reasons.append("generic_editorial_tone")
    if not overlap:
        reasons.append("no_context_overlap")
    if len(unsupported) >= 5 and len(overlap) < 2:
        reasons.append("unsupported_topic_drift")
    if not has_supported_phrase and len(overlap) < 2:
        reasons.append("weak_selected_angle_grounding")
    return reasons


def _plausibility_for_text(text: str) -> tuple[dict[str, Any], list[str]]:
    if not naturalness_gate_enabled():
        return {"enabled": False, "passes": True, "reasons": []}, []
    plausibility: dict[str, Any] = {"enabled": True, **comment_plausibility_gate(text)}
    raw_reasons = plausibility.get("reasons", [])
    extra = [str(reason) for reason in raw_reasons] if isinstance(raw_reasons, list) else []
    return plausibility, extra


def _candidate_overlap(
    text: str,
    *,
    post_augmentation: PostAugmentation | None,
    sample: dict[str, Any] | None,
    selected_angle: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str]]:
    normalized = " ".join(text.split()).lower()
    support_vocab = set(
        tokenize_content_words(
            " ".join(context_support_texts(post_augmentation, sample, selected_angle))
        )
    )
    candidate_tokens = tokenize_content_words(text)
    overlap = [token for token in candidate_tokens if token in support_vocab]
    unsupported = [token for token in candidate_tokens if token not in support_vocab]
    generic = [pattern for pattern in GENERIC_EDITORIAL_PATTERNS if pattern in normalized]
    return candidate_tokens, overlap, unsupported, generic


def contextuality_gate(
    text: str,
    *,
    post_augmentation: PostAugmentation | None,
    sample: dict[str, Any] | None,
    selected_angle: dict[str, Any],
) -> dict[str, Any]:
    normalized = " ".join(text.split()).lower()
    candidate_tokens, overlap, unsupported, generic_patterns = _candidate_overlap(
        text,
        post_augmentation=post_augmentation,
        sample=sample,
        selected_angle=selected_angle,
    )
    reasons = _base_contextuality_reasons(
        overlap=overlap,
        unsupported=unsupported,
        generic_patterns=generic_patterns,
        has_supported_phrase=_has_supported_angle_phrase(
            overlap=overlap, sample=sample, selected_angle=selected_angle
        ),
    )
    reasons.extend(
        _barb_stance_reasons(
            text,
            normalized=normalized,
            candidate_tokens=candidate_tokens,
            post_augmentation=post_augmentation,
        )
    )
    plausibility, plausibility_reasons = _plausibility_for_text(text)
    reasons.extend(plausibility_reasons)
    return {
        "passes": not reasons,
        "overlap_tokens": overlap[:12],
        "unsupported_tokens": unsupported[:12],
        "generic_patterns": generic_patterns,
        "plausibility": plausibility,
        "reasons": reasons,
    }
