"""Characterization tests for ``run_prep_until_google_quota_then_stego``.

The workflow's observable contract is its progress-event stream: the dashboard renders it and
overnight runs are diagnosed from it. These tests pin the event *sequence* and the payload
fields that carry state, so the phase decomposition can be rearranged without silently
changing what a consumer sees.

The two drain stop reasons covered here -- ``failed_post_without_id`` and
``repeat_failed_post`` -- had no coverage before; they are the loop's only guards against
spinning forever on a post that will not process.
"""

from typing import Any

import pytest

from workflows.runner import WorkflowRunner

_NO_POSTS = "No unprocessed posts found for step='final-step' and tag='version_42'."


class _QuotaOnSecondCallResearch:
    """Succeeds once, then reports the Google quota exhaustion that ends the prep phase."""

    def __init__(self) -> None:
        self.calls = 0

    def process_post_objects(
        self,
        posts: list[dict[str, Any]],
        step: str,
        disable_bing_fallback: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            return [{"id": "r1"}]
        raise RuntimeError("google search quota exceeded")


class _OneFailedAngleAngles:
    def __init__(self) -> None:
        self._last_batch_summary = {"failed_count": 1}

    def process_post_objects(self, posts: list[dict[str, Any]], step: str) -> list[dict[str, Any]]:
        return []


class _ScriptedStego:
    """Returns each scripted result in turn, then reports an empty queue."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = script
        self.calls = 0

    def process_post(
        self,
        post_id: str | None = None,
        payload: str | None = None,
        tag: str | None = None,
        list_offset: int = 0,
    ) -> dict[str, Any]:
        if self.calls >= len(self.script):
            raise ValueError(_NO_POSTS)
        result = self.script[self.calls]
        self.calls += 1
        return result


def _run_with_quota_handoff(
    stego_script: list[dict[str, Any]],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """Drive one prep batch, then a quota stop, then the scripted stego drain."""
    runner = WorkflowRunner.__new__(WorkflowRunner)
    events: list[tuple[str, dict[str, Any]]] = []
    runner.research = _QuotaOnSecondCallResearch()
    runner.gen_angles = _OneFailedAngleAngles()
    runner.stego = _ScriptedStego(stego_script)
    batches = iter([[{"id": "d1"}], [{"id": "d2"}]])
    runner.run_data_load = lambda count, offset=0, batch_size=5: next(batches)
    result = runner.run_prep_until_google_quota_then_stego(
        tag="version_42",
        batch_count=1,
        batch_size=5,
        on_progress=lambda event, payload: events.append((event, payload)),
    )
    return events, result


def _names(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [name for name, _ in events]


def _payloads(events: list[tuple[str, dict[str, Any]]], name: str) -> list[dict[str, Any]]:
    return [payload for event, payload in events if event == name]


def test_prep_phase_emits_one_stage_done_per_stage_in_order():
    events, _ = _run_with_quota_handoff([])

    stages = [payload["stage"] for payload in _payloads(events, "prep_stage_done")]
    assert stages == ["data-load", "research", "gen-angles", "data-load"]


def test_only_the_angles_stage_reports_failed_count():
    events, _ = _run_with_quota_handoff([])

    with_failed = {
        payload["stage"]
        for payload in _payloads(events, "prep_stage_done")
        if "failed_count" in payload
    }
    assert with_failed == {"gen-angles"}


def test_quota_stop_skips_the_batch_summary_for_that_iteration():
    events, _ = _run_with_quota_handoff([])

    # Iteration 2 hits quota after data-load, so it never reaches a batch summary.
    iterations = [payload["iteration"] for payload in _payloads(events, "prep_batch_summary")]
    assert iterations == [1]


def test_prep_totals_count_the_quota_iterations_data_load():
    _, result = _run_with_quota_handoff([])

    assert result["prep"] == {
        "iterations": 2,
        "data_load_processed": 2,
        "research_processed": 1,
        "gen_angles_processed": 0,
        "gen_angles_failed": 1,
        "prepared_posts": 0,
        "quota_detected": True,
        "stop_reason": "google_search_quota_detected",
    }


def test_phase_order_is_prep_then_transition_then_stego():
    events, _ = _run_with_quota_handoff([])

    phase_events = [
        (name, payload.get("phase") or payload.get("to_phase"))
        for name, payload in events
        if name in {"phase_start", "phase_done", "phase_transition"}
    ]
    assert phase_events == [
        ("phase_start", "prep"),
        ("phase_done", "prep"),
        ("phase_transition", "stego"),
        ("phase_start", "stego"),
        ("phase_done", "stego"),
    ]


def test_drain_stops_on_a_failed_post_with_no_id():
    events, result = _run_with_quota_handoff([{"succeeded": False, "retry_count": 2, "post": {}}])

    assert result["stego"]["stopped_reason"] == "failed_post_without_id"
    assert result["stego"]["processed_count"] == 1
    assert result["stego"]["failed_count"] == 1
    assert _payloads(events, "stego_post_done")[0]["post_id"] is None


def test_drain_stops_when_the_same_post_fails_twice():
    failure = {"succeeded": False, "retry_count": 1, "post": {"id": "p9"}}
    events, result = _run_with_quota_handoff([dict(failure), dict(failure)])

    assert result["stego"]["stopped_reason"] == "repeat_failed_post"
    assert result["stego"]["processed_count"] == 2
    summaries = _payloads(events, "stego_batch_summary")
    # Only the terminal summary carries a stop reason.
    assert "stop_reason" not in summaries[0]
    assert summaries[1]["stop_reason"] == "repeat_failed_post"


def test_drain_ends_cleanly_when_the_queue_empties():
    events, result = _run_with_quota_handoff(
        [{"succeeded": True, "retry_count": 0, "post": {"id": "p1"}}]
    )

    assert result["stego"]["stopped_reason"] == "no_unprocessed_posts"
    assert result["stego"]["succeeded_count"] == 1
    # The empty-queue stop emits no summary of its own.
    assert len(_payloads(events, "stego_batch_summary")) == 1
    assert _names(events)[-1] == "phase_done"


def test_a_failed_post_that_then_succeeds_does_not_stop_the_drain():
    _, result = _run_with_quota_handoff(
        [
            {"succeeded": False, "retry_count": 1, "post": {"id": "p1"}},
            {"succeeded": True, "retry_count": 0, "post": {"id": "p2"}},
        ]
    )

    assert result["stego"]["stopped_reason"] == "no_unprocessed_posts"
    assert result["stego"]["succeeded_count"] == 1
    assert result["stego"]["failed_count"] == 1


def test_non_quota_research_errors_are_not_swallowed():
    class _ExplodingResearch:
        def process_post_objects(
            self,
            posts: list[dict[str, Any]],
            step: str,
            disable_bing_fallback: bool = False,
        ) -> list[dict[str, Any]]:
            raise RuntimeError("research exploded")

    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner.research = _ExplodingResearch()
    runner.gen_angles = object()
    runner.stego = object()
    runner.run_data_load = lambda count, offset=0, batch_size=5: [{"id": "d1"}]

    with pytest.raises(RuntimeError, match="research exploded"):
        runner.run_prep_until_google_quota_then_stego(tag="version_42")
