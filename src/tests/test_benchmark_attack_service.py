from __future__ import annotations

from services.benchmark_attack_service import (
    attack_variants,
    delete_sentence,
    delete_words,
    mutate_context,
    reorder_sentences,
    substitute_synonyms,
)


def test_word_attacks_are_seeded_and_repeatable() -> None:
    text = "A quick brown fox jumps over a calm dog."

    def resolver(word: str) -> str | None:
        return {"quick": "fast", "calm": "quiet"}.get(word.lower())

    assert delete_words(text, 0.2, 7) == delete_words(text, 0.2, 7)
    assert substitute_synonyms(text, 0.2, 7, resolver) == substitute_synonyms(
        text, 0.2, 7, resolver
    )


def test_sentence_attacks_report_not_applicable_for_one_sentence() -> None:
    assert delete_sentence("Only one sentence.", 1) is None
    assert reorder_sentences("Only one sentence.", 1) is None
    assert mutate_context(["only one"], 1) is None


def test_attack_matrix_contains_all_declared_severities() -> None:
    rows = attack_variants(
        "First sentence. Second sentence.",
        ["context one", "context two"],
        9,
        lambda word: None,
        {"low": "Low rewrite.", "medium": "Medium rewrite.", "high": "High rewrite."},
    )

    attacks = {(row["attack"], str(row["severity"])) for row in rows}
    assert ("word_deletion", "0.05") in attacks
    assert ("synonym_substitution", "0.2") in attacks
    assert ("sentence_deletion", "1") in attacks
    assert ("sentence_reorder", "1") in attacks
    assert ("context_mutation", "1") in attacks
    assert ("llm_paraphrase", "high") in attacks
