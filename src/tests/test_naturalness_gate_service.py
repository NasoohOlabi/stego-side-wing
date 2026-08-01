"""Regression tests for the shared naturalness gate.

Every case here is a rule the deployed ZLG gate got wrong in the scale300 run,
pinned so a future retune cannot quietly reintroduce it.
"""

from __future__ import annotations

from services import naturalness_gate_service as gate

#: Ordinary human Reddit comments. None of these should be rejected: each one
#: trips a rule the deployed gate enforced.
HUMAN_COMMENTS = (
    "The US government is offering tax benefits to Hyundai for setting up a plant in the US.",
    "He was shot in a vital spot and it's not like he was wearing a vest.",
    "The FDA approved it for headaches in the third trimester but not in the first two.",
    "It’s strange to think that if the events had occurred decades ago, "
    "they would have been considered the most shocking event of the day.",
    "The state is actively appealing the ruling on 'EdChoice' and funding private schools.",
    "The media narrative is so powerful that the narrative is what people remember…",
    "I think the point of the article is that the cost of the program keeps rising.",
)


def test_smart_apostrophes_are_not_a_rejection_reason() -> None:
    """28 of 133 gate rejections were this, and not one was real mojibake."""
    outcome = gate.evaluate_naturalness(
        "It’s strange that the White House didn’t address the report before Friday…"
    )
    assert outcome.passed
    assert outcome.metrics.replacement_char_count == 0

    # The same sentence with ASCII typography must score identically -- the
    # curly forms carry no signal the gate should act on.
    ascii_form = gate.evaluate_naturalness(
        "It's strange that the White House didn't address the report before Friday..."
    )
    assert ascii_form.metrics == outcome.metrics


def test_contractions_do_not_read_as_unclosed_quotes() -> None:
    outcome = gate.evaluate_naturalness(
        "He was shot in a vital spot and it's not like he was wearing a vest."
    )
    assert outcome.passed
    assert outcome.metrics.unbalanced_quote_count == 0


def test_a_repeated_bigram_is_normal_english() -> None:
    """The deployed limit was 2, which fails any comment repeating 'of the'."""
    outcome = gate.evaluate_naturalness(
        "The cost of the program and the scale of the rollout are both underestimated here."
    )
    assert outcome.metrics.max_bigram_repeat >= 2
    assert outcome.passed


def test_real_replacement_characters_still_fail() -> None:
    outcome = gate.evaluate_naturalness(
        "The teacher�s snake was feeding week old kittens to the student."
    )
    assert not outcome.passed
    assert "replacement_char" in outcome.failed_rules


def test_thinking_block_leakage_fails() -> None:
    outcome = gate.evaluate_naturalness(
        "It's a good time to start planting.\n\n<think>\nThinking Process:\n\n1."
    )
    assert not outcome.passed
    assert "structural_artifact" in outcome.failed_rules


def test_meta_commentary_leakage_fails() -> None:
    """The deployed gate accepted these; 22 of its 304 'successes' were this."""
    outcome = gate.evaluate_naturalness(
        "[The user wants you to generate a short comment on a Reddit news discussion.]"
    )
    assert not outcome.passed
    assert "structural_artifact" in outcome.failed_rules


def test_unclosed_double_quote_fails() -> None:
    outcome = gate.evaluate_naturalness(
        '"The fact that we have so much money in our bank accounts, '
        "yet we are still struggling to make ends meet."
    )
    assert not outcome.passed
    assert "unbalanced_quote" in outcome.failed_rules


def test_degenerate_repetition_still_fails() -> None:
    outcome = gate.evaluate_naturalness(
        "The guy who is the most famous person on earth is also the most famous person on earth."
    )
    assert not outcome.passed


def test_gate_passes_ordinary_human_comments() -> None:
    """The calibration property: the gate must not reject real writing.

    scripts/calibrate_naturalness_gate.py runs this against a full corpus; this
    test pins the specific sentences that motivated each threshold.
    """
    assert gate.rejection_rate(list(HUMAN_COMMENTS)) == 0.0


def test_thresholds_are_frozen_and_validated() -> None:
    thresholds = gate.NaturalnessThresholds()
    assert thresholds.max_bigram_repeat_limit == 4
    assert thresholds.max_words == 60
    tightened = gate.NaturalnessThresholds(max_bigram_repeat_limit=2)
    assert not gate.evaluate_naturalness(
        "The cost of the program and the scale of the rollout are both underestimated here.",
        tightened,
    ).passed


#: Verbatim output from live /hide probes against the recalibrated server. Every
#: one of these was returned as a "comment" and passed the gate before the
#: instruction_echo rule existed -- meta-text is fluent, so nothing else caught it.
INSTRUCTION_ECHOES = (
    'Do not start with "Here is", "Sure", or similar phrases.',
    "Do not include any extra text or preamble after your answer.",
    "Do not start with 'Sure', 'Okay'. Do not add a period at the end.",
    'Do not start with "The," or any other introductory phrases.',
    "Only the comment text itself is allowed as output (including any punctuation "
    "and whitespace).",
    'Do not start with "Here is a comment". Just give me the text of the comment.',
)


def test_instruction_echo_is_rejected() -> None:
    for echo in INSTRUCTION_ECHOES:
        outcome = gate.evaluate_naturalness(echo)
        assert not outcome.passed, echo
        assert "instruction_echo" in outcome.failed_rules, echo


def test_instruction_echo_does_not_fire_on_ordinary_speech() -> None:
    """"Do not" alone is normal Reddit phrasing; the rule needs task vocabulary.

    Measured at zero false positives across the 908 human cover sentences and the
    304 accepted ZLG samples in the scale300 run.
    """
    for comment in HUMAN_COMMENTS:
        assert gate.score_naturalness(comment).instruction_echo_count == 0, comment
    ordinary = (
        "Do not trust anything that account posts, it has been wrong every single time.",
        "I never start with the cheapest option because it always costs more later.",
    )
    for comment in ordinary:
        assert gate.evaluate_naturalness(comment).passed, comment


#: Echoes the first smoke run recorded as *accepted* ZLG samples, which the
#: initial pattern set missed: "the comment text itself" has a word between the
#: two halves, and "Start directly with..." carries no other task vocabulary.
SMOKE_RUN_ACCEPTED_ECHOES = (
    "Do not add extra newlines between sentences or paragraphs in your output.",
    "Only the comment text itself is needed as the output format for this request.",
    "Do not include any other output or text outside of the comment itself.",
    'Start directly with the "This". Start immediately.',
    "Only the comment text itself in one plain sentence without any extra formatting "
    "or explanation.",
)


def test_smoke_run_accepted_echoes_are_rejected() -> None:
    for echo in SMOKE_RUN_ACCEPTED_ECHOES:
        outcome = gate.evaluate_naturalness(echo)
        assert not outcome.passed, echo
        assert "instruction_echo" in outcome.failed_rules, echo


def test_backend_error_leak_is_rejected() -> None:
    """A different failure mode: not the model talking about its task, but the
    inference backend itself failing and the error string being embedded as if it
    were the comment. Seen verbatim 10 times in 510 accepted samples of the
    recalibrated scale300 re-run -- fluent enough (10 words, no repetition, no
    unbalanced quotes) that it was the only rule set that could have caught it."""
    blob = '{"index": "error": true}, {"content": null}] [ERROR: Failed to call LLM.'
    outcome = gate.evaluate_naturalness(blob)
    assert not outcome.passed
    assert "backend_error" in outcome.failed_rules


def test_backend_error_pattern_does_not_fire_on_ordinary_speech() -> None:
    for comment in HUMAN_COMMENTS:
        assert gate.score_naturalness(comment).backend_error_count == 0, comment
