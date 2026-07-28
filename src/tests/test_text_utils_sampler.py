"""Focused tests for deterministic source-aware dictionary sampling."""

from typing import Any

from workflows.utils.text_utils import build_post_text_dictionary_bundle


def _sample_post() -> dict[str, Any]:
    return {
        "selftext": "post body",
        "search_results": [
            {"link": "https://example.test/a", "text": "Alpha research"},
            {"link": "https://example.test/b", "text": "Beta research"},
            {"link": "https://example.test/c", "text": "Gamma research"},
        ],
        "comments": [
            {"id": "c-a", "body": "Alpha comment"},
            {"id": "c-b", "body": "Beta comment"},
            {"id": "c-c", "body": "Gamma comment"},
        ],
    }


def test_sampler_is_stable_across_source_enumeration_order(
    monkeypatch: Any,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS", "2")
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_COMMENTS", "2")
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", "5")
    post = _sample_post()
    reversed_post = {
        **post,
        "search_results": list(reversed(post["search_results"])),
        "comments": list(reversed(post["comments"])),
    }

    first = build_post_text_dictionary_bundle(post, apply_capacity_profile=True)
    second = build_post_text_dictionary_bundle(
        reversed_post,
        apply_capacity_profile=True,
    )

    assert first["texts"] == second["texts"]
    assert first["report"]["dictionary_id"] == second["report"]["dictionary_id"]
    assert first["texts"][0] == "post body"
    assert [entry["source"] for entry in first["entries"][1:]] == [
        "search_results",
        "comments",
        "search_results",
        "comments",
    ]


def test_sampler_report_preserves_source_ids_and_selected_counts(
    monkeypatch: Any,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS", "1")
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_COMMENTS", "1")
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", "3")

    result = build_post_text_dictionary_bundle(
        _sample_post(),
        apply_capacity_profile=True,
    )

    assert result["report"]["sampler_version"] == "stable_round_robin_v1"
    assert result["report"]["selection_strategy"] == (
        "stable_source_rank_round_robin"
    )
    assert result["report"]["selected_source_counts"] == {
        "post": 1,
        "search_results": 1,
        "comments": 1,
    }
    selected = result["report"]["sample_entries"]
    assert selected[1]["source_id"].startswith("https://example.test/")
    assert selected[2]["source_id"].startswith("c-")


def test_sampler_retains_post_when_global_budget_is_zero(
    monkeypatch: Any,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", "0")

    result = build_post_text_dictionary_bundle(
        _sample_post(),
        apply_capacity_profile=True,
    )

    assert result["texts"] == ["post body"]


def test_exhaustive_report_still_identifies_sampler() -> None:
    result = build_post_text_dictionary_bundle(
        {"selftext": "post body"},
        apply_capacity_profile=False,
    )

    assert result["report"]["sampler_version"] == "stable_round_robin_v1"
    assert result["report"]["selection_strategy"] == "exhaustive_source_order"
