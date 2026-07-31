"""Shared naturalness gate for stego text, applied identically to every method.

The ZLG service applies its own gate server-side and rejects a hide outright when
it fails, while our method's output was never gated at all. That asymmetry made a
gate-configuration artifact look like a method result: in the scale300 run the
deployed gate rejected 133 of 554 ZLG samples, and re-deriving its rules from the
304 accepted vs 133 rejected split showed two of them fire on ordinary English --
any non-ASCII character (every occurrence was a typographic apostrophe, never
mojibake) and any word-bigram repeated twice ("of the"). Applying just those rules
to the 914 human cover sentences in the same run rejects 23.3% of real comments.

This module is the single definition of the gate: recalibrated thresholds, applied
to both methods, and calibrated against human text rather than against whatever the
generator happens to produce. The ZLG service is expected to converge on these
same rules so its 422s mean "could not encode", not "wrote an apostrophe".
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field, validate_call

WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)

REPLACEMENT_CHAR = "�"

#: Typographic characters that carry no signal a naturalness gate should act on.
#: NFKC leaves curly quotes and dashes alone, so they are folded explicitly.
#: Keyed by codepoint so this table cannot introduce the characters it removes.
_PUNCTUATION_FOLDING: dict[int, str] = {
    0x2018: "'",  # left single quotation mark
    0x2019: "'",  # right single quotation mark -- the apostrophe that failed 28 samples
    0x201C: '"',  # left double quotation mark
    0x201D: '"',  # right double quotation mark
    0x2026: "...",  # horizontal ellipsis
    0x2013: "-",  # en dash
    0x2014: "-",  # em dash
    0x00A0: " ",  # no-break space
}

#: Unambiguous generation artifacts. A leading "- " list marker is deliberately
#: absent: it is cosmetic, it is what the prompt change already targets, and it
#: fired on 27 of 908 human comments (Reddit list items) for no diagnostic gain.
STRUCTURAL_ARTIFACT_PATTERNS = (
    r"<think>",
    r"</think>",
    r"\*\*",
    r"^\s*\[",
    r"<\|im_end\|>",
    r"</?OUTPUT>",
    r"\[INST\]",
)


class NaturalnessThresholds(BaseModel):
    """Gate limits. Defaults are calibrated to pass >95% of human Reddit comments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_words: int = Field(default=8, ge=1)
    max_words: int = Field(default=60, ge=1)
    # The deployed ZLG gate used 2, which fails any comment repeating one bigram.
    max_bigram_repeat_limit: int = Field(default=4, ge=2)
    repetition_ratio_limit: float = Field(default=0.28, gt=0.0, le=1.0)


class NaturalnessMetrics(BaseModel):
    """Raw measurements, kept separate from the pass/fail decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    word_count: int
    repetition_ratio: float
    single_token_share: float
    max_bigram_repeat: int
    unbalanced_quote_count: int
    replacement_char_count: int
    structural_artifact_count: int
    terminal_punctuation: bool
    decode_ready: bool


class NaturalnessOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    failed_rules: tuple[str, ...]
    metrics: NaturalnessMetrics


def normalize_for_scoring(text: str) -> str:
    """Fold typography that a naturalness gate has no business rejecting.

    Smart quotes and ellipses are what the deployed gate's `has_non_ascii` rule
    was actually catching. Genuine replacement characters and control codes
    survive normalization and are still counted, because those do indicate a
    broken decode.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_PUNCTUATION_FOLDING)
    return "".join(ch for ch in folded if ch == "\n" or unicodedata.category(ch) != "Cc")


def _tokens(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def _repetition(words: list[str]) -> tuple[float, float]:
    if not words:
        return 0.0, 0.0
    counts = Counter(words)
    repeats = sum(count - 1 for count in counts.values())
    return repeats / len(words), max(counts.values()) / len(words)


def _max_bigram_repeat(words: list[str]) -> int:
    return max(Counter(pairwise(words)).values(), default=0)


def _unbalanced_quotes(text: str) -> int:
    """Only double quotes. A single quote is overwhelmingly an apostrophe.

    Counting `'` parity here rejected 261 of 908 human comments -- every
    contraction ("it's") reads as an unclosed quote.
    """
    return 1 if text.count('"') % 2 == 1 else 0


def _structural_artifacts(text: str) -> int:
    return sum(
        1
        for pattern in STRUCTURAL_ARTIFACT_PATTERNS
        if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    )


@validate_call
def score_naturalness(text: str) -> NaturalnessMetrics:
    """Measure one candidate. Pure: no thresholds applied here."""
    normalized = normalize_for_scoring(text)
    stripped = normalized.strip()
    words = _tokens(normalized)
    repetition_ratio, single_token_share = _repetition(words)
    return NaturalnessMetrics(
        word_count=len(words),
        repetition_ratio=repetition_ratio,
        single_token_share=single_token_share,
        max_bigram_repeat=_max_bigram_repeat(words),
        unbalanced_quote_count=_unbalanced_quotes(stripped),
        replacement_char_count=normalized.count(REPLACEMENT_CHAR),
        structural_artifact_count=_structural_artifacts(stripped),
        terminal_punctuation=stripped.endswith((".", "!", "?", '"', "'", ")")),
        decode_ready=bool(stripped),
    )


def _rule_violations(
    metrics: NaturalnessMetrics, thresholds: NaturalnessThresholds
) -> tuple[str, ...]:
    checks = (
        ("not_decode_ready", not metrics.decode_ready),
        ("too_short", metrics.word_count < thresholds.min_words),
        ("too_long", metrics.word_count > thresholds.max_words),
        ("bigram_repeat", metrics.max_bigram_repeat >= thresholds.max_bigram_repeat_limit),
        ("degenerate_repetition", metrics.repetition_ratio >= thresholds.repetition_ratio_limit),
        ("unbalanced_quote", metrics.unbalanced_quote_count > 0),
        ("replacement_char", metrics.replacement_char_count > 0),
        ("structural_artifact", metrics.structural_artifact_count > 0),
    )
    return tuple(name for name, violated in checks if violated)


@validate_call
def evaluate_naturalness(
    text: str, thresholds: NaturalnessThresholds | None = None
) -> NaturalnessOutcome:
    """Score `text` and apply the gate. The one entry point both methods use."""
    limits = thresholds or NaturalnessThresholds()
    metrics = score_naturalness(text)
    failed = _rule_violations(metrics, limits)
    return NaturalnessOutcome(passed=not failed, failed_rules=failed, metrics=metrics)


@validate_call
def rejection_rate(texts: list[str], thresholds: NaturalnessThresholds | None = None) -> float:
    """Share of `texts` the gate rejects. Used to calibrate against human comments."""
    if not texts:
        return 0.0
    rejected = sum(1 for text in texts if not evaluate_naturalness(text, thresholds).passed)
    return rejected / len(texts)
