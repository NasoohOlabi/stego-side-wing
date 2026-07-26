"""Application-scoped access to the workflow runner."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from flask import Flask, current_app
from werkzeug.local import LocalProxy

if TYPE_CHECKING:
    from services.workflow_facade import WorkflowRunner

from services.workflow_facade import WorkflowRunner

_WORKFLOW_RUNNER_CONFIG_KEY = "WORKFLOW_RUNNER"


def init_workflow_runner(app: Flask) -> None:
    """Install the application's default runner when one was not injected."""
    app.config.setdefault(_WORKFLOW_RUNNER_CONFIG_KEY, WorkflowRunner())


def get_workflow_runner() -> WorkflowRunner:
    """Return the runner injected into the active Flask application."""
    runner = current_app.config.get(_WORKFLOW_RUNNER_CONFIG_KEY)
    if runner is None:
        raise RuntimeError("WORKFLOW_RUNNER is not configured")
    return cast("WorkflowRunner", runner)


# Routes resolve this proxy only while handling a request, so it never creates or
# retains a process-wide runner instance.
runner: WorkflowRunner = LocalProxy(get_workflow_runner)  # type: ignore[assignment]
