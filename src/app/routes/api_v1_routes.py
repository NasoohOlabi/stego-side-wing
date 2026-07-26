"""Backward-compatible entrypoint for API v1 (implementation in ``app.routes.api_v1``).

Re-exports symbols that tests monkeypatch on this module (see ``src/tests/test_api_v1_*``).
"""

from app.routes.api_v1 import bp, init_workflow_runner
from infrastructure.json_logging import clear_api_log_file, get_api_log_file_stats
from services.recent_updates_service import get_recent_git_updates
from services.stego_metrics_service import (
    delete_metrics_output_sample,
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

__all__ = [
    "bp",
    "clear_api_log_file",
    "default_workflow_llm_prompts",
    "delete_metrics_output_sample",
    "get_api_log_file_stats",
    "get_prompts",
    "get_recent_git_updates",
    "init_workflow_runner",
    "list_metrics_history",
    "reload_prompts",
    "run_divergence_metrics",
    "run_perplexity_metrics",
    "run_single_post_metrics",
    "save_workflow_llm_prompts_to_path",
    "workflow_llm_prompts_path",
]
