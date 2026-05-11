from pathlib import Path

from services.stego_feedback_service import (
    AdaptiveSampleState,
    StegoFeedbackRun,
    classify_failure,
    plan_adaptive_action,
    summarize_input_post,
)


def test_classify_failure_normalizes_common_decode_errors() -> None:
    assert (
        classify_failure("RuntimeError: Stego LLM output must be valid JSON")
        == "stego_invalid_json"
    )
    assert (
        classify_failure("RuntimeError: Receiver decoded a different payload than expected")
        == "receiver_payload_mismatch"
    )
    assert (
        classify_failure("RuntimeError: Decoding validation failed")
        == "receiver_angle_mismatch"
    )


def test_classify_failure_uses_stage_envelope_for_data_failures() -> None:
    envelope = {
        "data_load": {"has_selftext": False},
        "research": {"search_results_count": 0},
        "gen_angles": {"angles_count": 0},
    }

    assert classify_failure("RuntimeError: failed", envelope=envelope) == "data_load_empty_selftext"


def test_plan_adaptive_action_mutates_sample_state_once_per_strategy() -> None:
    state = AdaptiveSampleState()

    first = plan_adaptive_action("receiver_payload_recovery_failed", state)
    second = plan_adaptive_action("receiver_payload_recovery_failed", state)

    assert first == {"action": "expand_payload_recovery_padding", "max_padding_bits": 1024}
    assert second is None
    assert state.max_padding_bits == 1024


def test_summarize_input_post_reports_low_angle_diversity() -> None:
    post = {
        "url": "https://example.test/a",
        "selftext": "article text",
        "search_results": [],
        "angles": [
            {"category": "c", "source_quote": "q", "tangent": "t"},
            {"category": "c", "source_quote": "q", "tangent": "t"},
        ],
    }

    summary = summarize_input_post(post, {"selftext": "baseline"})

    assert summary["data_load"]["has_selftext"] is True
    assert summary["research"]["low_quality"] is True
    assert summary["gen_angles"]["low_diversity"] is True


def test_feedback_run_writes_clusters_and_leaderboard(tmp_path: Path) -> None:
    feedback = StegoFeedbackRun(tmp_path)
    feedback.record_event(
        {
            "outcome": "failed",
            "failure_code": "receiver_angle_mismatch",
            "post_id": "p1",
        }
    )

    leaderboard = feedback.finalize(
        {
            "total_requested_samples": 2,
            "total_succeeded_samples": 1,
            "total_failed_samples": 1,
            "profile_summaries": [
                {
                    "variant": "balanced",
                    "requested_samples": 2,
                    "samples_succeeded": 1,
                    "samples_failed": 1,
                    "summary_metrics": {"quality_metrics": {"perplexity": 12.0}},
                }
            ],
        }
    )

    assert leaderboard["failure_rate"] == 0.5
    assert (tmp_path / "events.jsonl").is_file()
    assert (tmp_path / "failure_clusters.json").is_file()
    assert (tmp_path / "leaderboard.json").is_file()

