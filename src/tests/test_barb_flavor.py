"""Unit tests for Project BARB style resolution, stance gate, and pairer join."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from infrastructure.config import get_workflow_barb_stance_gate, get_workflow_stego_prompt_style
from workflows.pipelines import stego_contextuality as contextuality
from workflows.utils.workflow_llm_prompts import stego_encode_prompts_for_style


def _load_pairer():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "build_barb_variant_comparison_dataset.py"
    )
    spec = importlib.util.spec_from_file_location("build_barb_pairer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_barb_style_resolution_returns_barb_templates(
    clear_workflow_capacity_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_STEGO_PROMPT_STYLE", "barb")
    assert get_workflow_stego_prompt_style() == "barb"
    prompts = stego_encode_prompts_for_style("barb")
    assert "BARB CONTRACT" in prompts.system_template
    assert "strong felt opinion" in prompts.system_template
    assert "Do NOT default every reply to sarcasm" in prompts.system_template
    assert "hidden routing hint" in prompts.user_template
    natural = stego_encode_prompts_for_style("natural")
    assert prompts.system_template != natural.system_template


def test_barb_stance_gate_off_by_default(clear_workflow_capacity_env: None) -> None:
    assert get_workflow_barb_stance_gate() is False


def test_barb_stance_gate_rejects_safe_universal(
    clear_workflow_capacity_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_BARB_STANCE_GATE", "1")
    monkeypatch.setenv("WORKFLOW_NATURALNESS_GATE_ENABLED", "0")
    post_augmentation = {
        "commentEmbedding": {
            "context": {
                "title": "City council votes on housing cap",
                "selftext": "Local officials debated a strict housing limit tonight.",
            },
            "pickedCommentChain": [{"name": "u1", "body": "This housing cap feels cruel."}],
        }
    }
    sample = {
        "source_quote": "This housing cap feels cruel.",
        "tangent": "housing policy cruelty",
        "category": "Politics",
        "best_match": "housing cap debate",
    }
    result = contextuality.contextuality_gate(
        "Wouldn't it be nice if we could all just live in the same country and get along.",
        post_augmentation=post_augmentation,
        sample=sample,
        selected_angle=sample,
    )
    assert result["passes"] is False
    assert "safe_universal_sentiment" in result["reasons"]


def test_barb_stance_gate_rejects_weak_thread_specificity(
    clear_workflow_capacity_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_BARB_STANCE_GATE", "1")
    monkeypatch.setenv("WORKFLOW_NATURALNESS_GATE_ENABLED", "0")
    post_augmentation = {
        "commentEmbedding": {
            "context": {
                "title": "City council votes on housing cap",
                "selftext": "Local officials debated a strict housing limit tonight.",
            },
            "pickedCommentChain": [{"name": "u1", "body": "This housing cap feels cruel."}],
        }
    }
    sample = {
        "source_quote": "generic feelings about society",
        "tangent": "broader civic mood",
        "category": "Culture",
        "best_match": "people feeling disconnected lately",
    }
    result = contextuality.contextuality_gate(
        "yeah people just feel weird about stuff lately and it is what it is",
        post_augmentation=post_augmentation,
        sample=sample,
        selected_angle=sample,
    )
    assert "weak_thread_specificity" in result["reasons"]


def test_barb_stance_gate_inactive_without_env(
    clear_workflow_capacity_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_NATURALNESS_GATE_ENABLED", "0")
    post_augmentation = {
        "commentEmbedding": {
            "context": {"title": "Title", "selftext": "Body text about widgets."},
            "pickedCommentChain": [{"name": "u1", "body": "Widgets again."}],
        }
    }
    sample = {
        "source_quote": "Widgets again.",
        "tangent": "widget fatigue",
        "category": "Tech",
        "best_match": "Widgets again.",
    }
    result = contextuality.contextuality_gate(
        "We could all just live in the same country and chill.",
        post_augmentation=post_augmentation,
        sample=sample,
        selected_angle=sample,
    )
    assert "safe_universal_sentiment" not in result["reasons"]


def test_pairer_join_key_and_inner_join() -> None:
    module = _load_pairer()
    assert module.pair_key("abc", 2) == ("abc", 2)
    assert module.pair_key("abc", "3") == ("abc", 3)

    balanced = {
        "entries": [
            {"post_id": "p1", "sample_index": 0, "output_file": None},
            {"post_id": "p2", "sample_index": 0, "output_file": None},
        ],
        "failures": [],
    }
    barb = {
        "entries": [{"post_id": "p1", "sample_index": 0, "output_file": None}],
        "failures": [
            {
                "post_id": "p3",
                "sample_index": 0,
                "failure_code": "generation_failure",
                "error": "boom",
            }
        ],
    }
    rows = module.join_variant_lanes(balanced, barb)
    assert len(rows) == 2
    assert {row["method"] for row in rows} == {"balanced", "barb"}
    assert all(row["post_id"] == "p1" for row in rows)
    assert all(row["sample_index"] == 0 for row in rows)
    assert all(row["itt_success"] is True for row in rows)


def test_pareto_manifest_includes_barb_variant() -> None:
    from services.stego_experiment_service import resolve_experiment_variants

    variant = resolve_experiment_variants(["barb"])[0]
    assert variant.name == "barb"
    assert variant.base_profile == "balanced"
    assert variant.env_overrides["WORKFLOW_STEGO_PROMPT_STYLE"] == "barb"
    assert variant.env_overrides["WORKFLOW_BARB_STANCE_GATE"] == "1"
