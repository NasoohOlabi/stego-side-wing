"""API v1 route package: registers handlers on ``blueprint.bp``."""

from __future__ import annotations

# Side-effect imports register @bp.route handlers
from app.routes.api_v1 import (
    routes_health,  # noqa: F401
    routes_kv_admin,  # noqa: F401
    routes_tools,  # noqa: F401
    routes_workflows,  # noqa: F401
)
from app.routes.api_v1.blueprint import bp
from app.routes.api_v1.runner_access import init_workflow_runner, runner

__all__ = ["bp", "init_workflow_runner", "runner"]
