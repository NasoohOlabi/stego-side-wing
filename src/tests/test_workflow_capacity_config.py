"""Tests for workflow capacity env resolution."""

import pytest

from infrastructure.config import (
    WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED,
    get_workflow_angles_max_input_blocks,
    get_workflow_angles_max_output,
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
from workflows.utils.capacity_observability import build_workflow_capacity_observation_fields


def test_workflow_capacity_defaults_to_uncapped_mid_profile(
    clear_workflow_capacity_env: None,
) -> None:
    assert get_workflow_capacity_settings()["limits_enabled"] is False
    assert get_workflow_capacity_profile() == "mid"
    assert get_workflow_research_max_terms() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_research_max_selected_urls() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_dictionary_max_search_results() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_dictionary_max_comments() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_angles_max_input_blocks() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_angles_max_output() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED


@pytest.mark.parametrize(
    ("profile", "expected_terms", "expected_urls", "expected_blocks"),
    (
        ("low", 4, 12, 24),
        ("high", 12, 96, 192),
        ("weird", 8, 48, 96),
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
    monkeypatch.setenv("WORKFLOW_CAPACITY_LIMITS_ENABLED", "1")
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
        "limits_enabled": False,
        "profile": "low",
        "research_max_terms": 3,
        "research_max_selected_urls": 9,
        "dictionary_max_search_results": 5,
        "dictionary_max_comments": 7,
        "angles_max_input_blocks": 11,
        "angles_max_output": 13,
    }


def test_workflow_capacity_limits_disabled_uses_unbounded_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_CAPACITY_LIMITS_ENABLED", "0")
    assert get_workflow_research_max_terms() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_research_max_selected_urls() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_dictionary_max_search_results() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_dictionary_max_comments() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_angles_max_input_blocks() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    assert get_workflow_angles_max_output() == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
    s = get_workflow_capacity_settings()
    assert s["limits_enabled"] is False
    assert s["research_max_terms"] == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED


def test_workflow_capacity_explicit_override_still_applies_when_limits_disabled(
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_CAPACITY_LIMITS_ENABLED", "0")
    monkeypatch.setenv("WORKFLOW_RESEARCH_MAX_TERMS", "7")
    assert get_workflow_research_max_terms() == 7


def test_build_workflow_capacity_observation_fields_merges_reports(
    clear_workflow_capacity_env: None,
) -> None:
    fields = build_workflow_capacity_observation_fields(
        research_report={
            "search_terms": ["a", "b"],
            "selected_results": [{"link": "x"}],
            "terms_report": {"terms_raw_count": 5, "terms_capped": True},
            "capacity": {"terms_capped": True, "selected_url_cap_hit": False},
        },
        gen_angles_report={
            "input_raw_count": 100,
            "input_count": 40,
            "input_raw_source_counts": {"comments": 80, "search_results": 10},
            "angles_raw_count": 20,
            "options_count": 16,
            "angles_capped": True,
            "input_capacity_applied": True,
        },
    )
    assert fields["event"] == "workflow_capacity_observation"
    assert fields["observed"]["terms_raw_count"] == 5
    assert fields["observed"]["unique_urls_selected"] == 1
    assert fields["observed"]["dictionary_raw_entry_count"] == 100
    assert fields["observed"]["angles_raw_count"] == 20
    assert "effective_limits" in fields


def test_research_fetch_budget_code_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_RESEARCH_FETCH_TIMEOUT_SEC", "90")
    monkeypatch.setenv("WORKFLOW_RESEARCH_FETCH_RETRIES", "2")
    assert get_workflow_research_fetch_timeout_sec() == 180.0
    assert get_workflow_research_fetch_retries() == 1
    assert get_workflow_research_fetch_concurrency() == 3
