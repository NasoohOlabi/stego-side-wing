"""Tests for the app -> workflows boundary.

``services.workflow_facade`` is the only sanctioned route from ``app`` into workflow
internals (``docs/development/architecture-layers.md`` rule 1), and it had no tests at all.
If a symbol silently drops off it, the failure surfaces as an ImportError deep in a route
at request time, so pin the surface here instead.
"""

from __future__ import annotations

import importlib

import pytest

from services import workflow_facade

# Everything app code is allowed to reach through the facade.
EXPECTED_SURFACE = {
    "WorkflowRunner",
    "WorkflowLlmPromptsDocument",
    "default_workflow_llm_prompts",
    "get_prompts",
    "reconcile_stale_double_process_claim_vs_explicit",
    "reload_prompts",
    "save_workflow_llm_prompts_to_path",
    "try_read_double_process_claim",
    "workflow_llm_prompts_path",
    "stable_hash",
    "text_preview",
}


def test_facade_exports_the_expected_surface() -> None:
    assert set(workflow_facade.__all__) == EXPECTED_SURFACE


@pytest.mark.parametrize("name", sorted(EXPECTED_SURFACE))
def test_every_exported_name_resolves(name: str) -> None:
    assert getattr(workflow_facade, name) is not None


def test_double_process_claim_helpers_come_from_the_runner_utils() -> None:
    """These moved behind the facade to remove the only direct app -> workflows import."""
    rou = importlib.import_module("workflows.runner_orchestration_utils")

    assert workflow_facade.try_read_double_process_claim is rou.try_read_double_process_claim
    assert (
        workflow_facade.reconcile_stale_double_process_claim_vs_explicit
        is rou.reconcile_stale_double_process_claim_vs_explicit
    )


def test_routes_reach_workflow_internals_only_through_the_facade() -> None:
    """Guard rule 1 at the source level, independent of the import-linter contract."""
    routes = importlib.import_module("app.routes.api_v1.routes_workflows")
    source = importlib.import_module("inspect").getsource(routes)

    offending = [
        line.strip()
        for line in source.splitlines()
        if line.startswith(("from workflows", "import workflows"))
    ]
    assert offending == []
