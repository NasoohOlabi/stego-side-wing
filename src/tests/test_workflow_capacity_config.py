"""Tests for workflow capacity env resolution."""

import pytest

from infrastructure.config import (
    get_workflow_angles_max_output,
    get_workflow_angles_max_input_blocks,
    get_workflow_capacity_profile,
    get_workflow_capacity_settings,
    get_workflow_dictionary_max_comments,
    get_workflow_dictionary_max_search_results,
    get_workflow_research_fetch_concurrency,
    get_workflow_research_fetch_retries,
    get_workflow_research_fetch_timeout_sec,
    get_workflow_research_max_selected_urls,
    get_workflow_research_max_terms,
)


def test_workflow_capacity_defaults_to_mid(
    clear_workflow_capacity_env: None,
) -> None:
    assert get_workflow_capacity_profile() == "mid"
    assert get_workflow_research_max_terms() == 8
    assert get_workflow_research_max_selected_urls() == 24
    assert get_workflow_dictionary_max_search_results() == 24
    assert get_workflow_dictionary_max_comments() == 32
    assert get_workflow_angles_max_input_blocks() == 48
    assert get_workflow_angles_max_output() == 16


@pytest.mark.parametrize(
    ("profile", "expected_terms", "expected_urls", "expected_blocks"),
    (
        ("low", 4, 12, 24),
        ("high", 12, 48, 128),
        ("weird", 8, 24, 48),
    ),
)
def test_workflow_capacity_profile_presets(
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
    profile: str,
    expected_terms: int,
    expected_urls: int,
    expected_blocks: int,
) -> None:
    monkeypatch.setenv("WORKFLOW_CAPACITY_PROFILE", profile)
    assert get_workflow_research_max_terms() == expected_terms
    assert get_workflow_research_max_selected_urls() == expected_urls
    assert get_workflow_angles_max_input_blocks() == expected_blocks


def test_workflow_capacity_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_CAPACITY_PROFILE", "low")
    monkeypatch.setenv("WORKFLOW_RESEARCH_MAX_TERMS", "3")
    monkeypatch.setenv("WORKFLOW_RESEARCH_MAX_SELECTED_URLS", "9")
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS", "5")
    monkeypatch.setenv("WORKFLOW_DICTIONARY_MAX_COMMENTS", "7")
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_INPUT_BLOCKS", "11")
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "13")
    settings = get_workflow_capacity_settings()
    assert settings == {
        "profile": "low",
        "research_max_terms": 3,
        "research_max_selected_urls": 9,
        "dictionary_max_search_results": 5,
        "dictionary_max_comments": 7,
        "angles_max_input_blocks": 11,
        "angles_max_output": 13,
    }


def test_research_fetch_budget_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_RESEARCH_FETCH_TIMEOUT_SEC", "90")
    monkeypatch.setenv("WORKFLOW_RESEARCH_FETCH_RETRIES", "2")
    monkeypatch.setenv("WORKFLOW_RESEARCH_FETCH_CONCURRENCY", "6")
    assert get_workflow_research_fetch_timeout_sec() == 90.0
    assert get_workflow_research_fetch_retries() == 2
    assert get_workflow_research_fetch_concurrency() == 6
