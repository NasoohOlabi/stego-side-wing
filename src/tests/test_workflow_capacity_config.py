"""Tests for workflow capacity env resolution."""

import pytest

from infrastructure.config import (
    WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED,
    get_workflow_angles_max_input_blocks,
    get_workflow_angles_max_output,
    get_workflow_angles_raw_target,
    get_workflow_angles_raw_target_multiplier,
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


def test_workflow_capacity_defaults_to_bounded_mid_profile(
    clear_workflow_capacity_env: None,
) -> None:
    assert get_workflow_capacity_settings()["limits_enabled"] is True
    assert get_workflow_capacity_profile() == "mid"
    assert get_workflow_research_max_terms() == 8
    assert get_workflow_research_max_selected_urls() == 24
    assert get_workflow_dictionary_max_search_results() == 16
    assert get_workflow_dictionary_max_comments() == 48
    assert get_workflow_angles_max_input_blocks() == 64
    assert get_workflow_angles_max_output() == 32
    assert get_workflow_angles_raw_target_multiplier() == 4
    assert get_workflow_angles_raw_target() == 128


@pytest.mark.parametrize(
    ("profile", "expected_terms", "expected_urls", "expected_blocks"),
    [
        ("low", 4, 12, 32),
        ("high", 12, 48, 128),
        ("weird", 8, 24, 64),
    ],
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
        "limits_enabled": True,
        "codec_dictionary_limits_enabled": False,
        "profile": "low",
        "research_max_terms": 3,
        "research_max_selected_urls": 9,
        "dictionary_max_search_results": 5,
        "dictionary_max_comments": 7,
        "angles_max_input_blocks": 11,
        "angles_max_output": 13,
        "angles_raw_target_multiplier": 4,
        "angles_raw_target": 52,
        "tangent_db": {
            "builder": "legacy",
            "min_relevance": 0.12,
            "search_relevance_mult": 1.5,
            "max_similarity": 0.7,
            "min_size": 0,
            "semantic_dedup": False,
        },
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
    assert get_workflow_angles_raw_target() is None
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


def test_workflow_angles_raw_target_multiplier_override(
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "16")
    monkeypatch.setenv("WORKFLOW_ANGLES_RAW_TARGET_MULTIPLIER", "3")
    assert get_workflow_angles_raw_target_multiplier() == 3
    assert get_workflow_angles_raw_target() == 48


def test_workflow_angles_raw_target_multiplier_zero_clamps_to_one(
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
) -> None:
    monkeypatch.setenv("WORKFLOW_ANGLES_MAX_OUTPUT", "16")
    monkeypatch.setenv("WORKFLOW_ANGLES_RAW_TARGET_MULTIPLIER", "0")
    assert get_workflow_angles_raw_target_multiplier() == 1
    assert get_workflow_angles_raw_target() == 16


def test_workflow_capacity_limits_disabled_emits_warning(
    monkeypatch: pytest.MonkeyPatch,
    clear_workflow_capacity_env: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("WORKFLOW_CAPACITY_LIMITS_ENABLED", "0")
    monkeypatch.setattr(
        "infrastructure.config._capacity_unbounded_warning_emitted",
        False,
    )

    get_workflow_capacity_settings()

    assert "workflow_capacity_limits_disabled" in caplog.messages


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
