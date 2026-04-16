"""Backward-compatible entrypoint for API v1 (implementation in ``app.routes.api_v1``).

Re-exports symbols that tests monkeypatch on this module (see ``src/tests/test_api_v1_*``).
"""

from infrastructure.json_logging import clear_api_log_file, get_api_log_file_stats
from services.stego_metrics_service import (
    list_metrics_history,
    run_divergence_metrics,
    run_perplexity_metrics,
    run_single_post_metrics,
)
from services.workflow_facade import (
    default_workflow_llm_prompts,
    get_prompts,
    reload_prompts,
    save_workflow_llm_prompts_to_path,
    workflow_llm_prompts_path,
)

from app.routes.api_v1 import bp, init_workflow_runner, runner

__all__ = [
    "bp",
    "init_workflow_runner",
    "runner",
    "clear_api_log_file",
    "get_api_log_file_stats",
    "list_metrics_history",
    "run_divergence_metrics",
    "run_perplexity_metrics",
    "run_single_post_metrics",
    "default_workflow_llm_prompts",
    "get_prompts",
    "reload_prompts",
    "save_workflow_llm_prompts_to_path",
    "workflow_llm_prompts_path",
]
