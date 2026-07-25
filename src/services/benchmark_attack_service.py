"""Deterministic text and context attacks for publication benchmarks."""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from typing import Any

from pydantic import validate_call

SynonymResolver = Callable[[str], str | None]


def _word_spans(text: str) -> list[re.Match[str]]:
    return list(re.finditer(r"\b[A-Za-z][A-Za-z'-]*\b", text))


@validate_call
def delete_words(text: str, severity: float, seed: int) -> str:
    spans = _word_spans(text)
    count = min(len(spans), max(0, round(len(spans) * severity)))
    removed = set(random.Random(seed).sample(range(len(spans)), count)) if count else set()
    chunks: list[str] = []
    cursor = 0
    for index, span in enumerate(spans):
        chunks.append(text[cursor : span.start()])
        if index not in removed:
            chunks.append(span.group(0))
        cursor = span.end()
    chunks.append(text[cursor:])
    return re.sub(r"\s+([,.;!?])", r"\1", "".join(chunks)).strip()


@validate_call(config={"arbitrary_types_allowed": True})
def substitute_synonyms(text: str, severity: float, seed: int, resolver: SynonymResolver) -> str:
    spans = _word_spans(text)
    candidates = [(index, resolver(span.group(0))) for index, span in enumerate(spans)]
    candidates = [(index, synonym) for index, synonym in candidates if synonym]
    count = min(len(candidates), max(0, round(len(spans) * severity)))
    chosen = dict(random.Random(seed).sample(candidates, count)) if count else {}
    chunks: list[str] = []
    cursor = 0
    for index, span in enumerate(spans):
        chunks.extend((text[cursor : span.start()], str(chosen.get(index, span.group(0)))))
        cursor = span.end()
    chunks.append(text[cursor:])
    return "".join(chunks)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


@validate_call
def delete_sentence(text: str, seed: int) -> str | None:
    sentences = _sentences(text)
    if len(sentences) < 2:
        return None
    index = random.Random(seed).randrange(len(sentences))
    return " ".join(sentence for idx, sentence in enumerate(sentences) if idx != index)


@validate_call
def reorder_sentences(text: str, seed: int) -> str | None:
    sentences = _sentences(text)
    if len(sentences) < 2:
        return None
    shuffled = sentences[:]
    random.Random(seed).shuffle(shuffled)
    if shuffled == sentences:
        shuffled = shuffled[1:] + shuffled[:1]
    return " ".join(shuffled)


@validate_call
def mutate_context(context: list[str], seed: int) -> list[str] | None:
    if len(context) < 2:
        return None
    index = random.Random(seed).randrange(len(context))
    return [value for idx, value in enumerate(context) if idx != index]


def attack_variants(
    text: str,
    context: list[str],
    seed: int,
    resolver: SynonymResolver,
    paraphrases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset, severity in enumerate((0.05, 0.10, 0.20)):
        rows.append(
            {
                "attack": "word_deletion",
                "severity": severity,
                "text": delete_words(text, severity, seed + offset),
            }
        )
        rows.append(
            {
                "attack": "synonym_substitution",
                "severity": severity,
                "text": substitute_synonyms(text, severity, seed + offset, resolver),
            }
        )
    rows.append({"attack": "sentence_deletion", "severity": 1, "text": delete_sentence(text, seed)})
    rows.append(
        {"attack": "sentence_reorder", "severity": 1, "text": reorder_sentences(text, seed)}
    )
    rows.append(
        {
            "attack": "context_mutation",
            "severity": 1,
            "text": text,
            "context": mutate_context(context, seed),
        }
    )
    for severity, value in (paraphrases or {}).items():
        rows.append({"attack": "llm_paraphrase", "severity": severity, "text": value})
    for row in rows:
        row["applicable"] = row.get("text") is not None and (
            row.get("attack") != "context_mutation" or row.get("context") is not None
        )
    return rows
