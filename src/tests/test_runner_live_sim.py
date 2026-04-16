"""Tests for stego-receiver live sim (skip bad posts, search fallback helpers)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from workflows.pipelines.research import is_likely_google_quota_error
from workflows.runner import WorkflowRunner
from workflows.runner_orchestration_utils import (
    compressed_full_for_live_receiver,
    is_receiver_data_load_failure,
)


def test_is_likely_google_quota_error() -> None:
    assert is_likely_google_quota_error(RuntimeError("All 5 Google keys failed"))
    assert is_likely_google_quota_error(ValueError("quota exceeded"))
    assert not is_likely_google_quota_error(RuntimeError("network unreachable"))


def test_is_receiver_data_load_failure() -> None:
    assert is_receiver_data_load_failure(RuntimeError("Receiver data-load failed: x"))
    assert not is_receiver_data_load_failure(RuntimeError("other"))


def test_compressed_full_for_live_receiver_prefers_override() -> None:
    stego = {"embedding": {"compression": {"compressed": "1abc"}}}
    assert compressed_full_for_live_receiver(stego, "override") == "override"
    assert compressed_full_for_live_receiver(stego, None) == "1abc"
    assert compressed_full_for_live_receiver(stego, "  ") == "1abc"


def test_live_sim_skips_receiver_data_load_then_succeeds(tmp_path: Path) -> None:
    runner = WorkflowRunner()
    ok = {"succeeded": True, "stego": {}, "receiver": {"payload": "p"}, "simulation": {}}
    side = [
        RuntimeError("Receiver data-load failed: bad html"),
        ok,
    ]

    def _once(**kwargs: object) -> dict:
        if not side:
            raise AssertionError("unexpected extra call")
        n = side.pop(0)
        if isinstance(n, Exception):
            raise n
        return n

    with patch("workflows.runner.run_stego_receiver_live_sim_once", side_effect=_once):
        out = runner.run_stego_receiver_live_sim(
            "alice",
            post_id=None,
            list_offset=1,
            max_post_attempts=2,
            simulation_root=tmp_path,
        )
    assert out["succeeded"] is True
    assert out["receiver"]["payload"] == "p"
    assert len(out["skipped_posts"]) == 1
    assert out["skipped_posts"][0]["stage"] == "receiver_data_load"


def test_live_sim_raises_non_data_load_runtime_error(tmp_path: Path) -> None:
    runner = WorkflowRunner()

    def _once(**kwargs: object) -> dict:
        raise RuntimeError("receiver failed for other reasons")

    with patch("workflows.runner.run_stego_receiver_live_sim_once", side_effect=_once):
        with pytest.raises(RuntimeError, match="other reasons"):
            runner.run_stego_receiver_live_sim(
                "alice",
                post_id=None,
                max_post_attempts=1,
                simulation_root=tmp_path,
            )


def test_live_sim_skips_google_quota_then_succeeds(tmp_path: Path) -> None:
    runner = WorkflowRunner()
    ok = {"succeeded": True, "stego": {}, "receiver": {"payload": "p"}, "simulation": {}}
    side = [
        RuntimeError("All 5 Google keys failed"),
        ok,
    ]

    def _once(**kwargs: object) -> dict:
        if not side:
            raise AssertionError("unexpected extra call")
        n = side.pop(0)
        if isinstance(n, Exception):
            raise n
        return n

    with patch("workflows.runner.run_stego_receiver_live_sim_once", side_effect=_once):
        out = runner.run_stego_receiver_live_sim(
            "alice",
            post_id=None,
            list_offset=1,
            max_post_attempts=2,
            simulation_root=tmp_path,
        )
    assert out["succeeded"] is True
    assert len(out["skipped_posts"]) == 1
    assert out["skipped_posts"][0]["stage"] == "search_quota"
