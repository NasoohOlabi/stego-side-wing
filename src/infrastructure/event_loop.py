"""Event loop management - re-export from event_loop_manager for consistency."""
from event_loop_manager import (
    get_event_loop,
    is_event_loop_running,
    run_async,
    start_event_loop,
    stop_event_loop,
)

__all__ = [
    "get_event_loop",
    "is_event_loop_running",
    "run_async",
    "start_event_loop",
    "stop_event_loop",
]
