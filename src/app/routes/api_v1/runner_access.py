"""Shared `WorkflowRunner` instance (monkeypatchable by tests) and app registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask

if TYPE_CHECKING:
    from services.workflow_facade import WorkflowRunner

from services.workflow_facade import WorkflowRunner

runner: WorkflowRunner = WorkflowRunner()


def init_workflow_runner(app: Flask) -> None:
    """Bind the process-wide runner on the app for optional injection patterns."""
    app.config.setdefault("WORKFLOW_RUNNER", runner)
