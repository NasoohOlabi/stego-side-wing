import importlib.util
from pathlib import Path
from uuid import uuid4


def _load_runner_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_actual_workload_e2e.py"
    spec = importlib.util.spec_from_file_location("run_actual_workload_e2e", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_actual_workload_e2e_retries_transient_sample_failure(monkeypatch) -> None:
    module = _load_runner_module()
    temp_root = Path(__file__).resolve().parents[2] / "metrics" / "test-run-actual-workload-e2e"
    temp_root.mkdir(parents=True, exist_ok=True)
    run_root = temp_root / f"actual-workload-e2e-{uuid4().hex}"
    run_root.mkdir()
    angles_dir = run_root / "angles"
    dataset_dir = run_root / "dataset"
    angles_dir.mkdir()
    dataset_dir.mkdir()

    (angles_dir / "post-1.json").write_text(
        '{"angles":[[{"text":"angle"}]],"title":"post"}', encoding="utf-8"
    )
    (dataset_dir / "post-1.json").write_text(
        '{"angles":[[{"text":"angle"}]],"title":"post"}', encoding="utf-8"
    )

    calls = {"encode": 0}

    class FakeStegoPipeline:
        def encode(self, *, payload: str, post: dict, tag: str, max_retries: int) -> dict:
            del payload, post, tag, max_retries
            calls["encode"] += 1
            if calls["encode"] == 1:
                return {
                    "succeeded": False,
                    "stego_text": "",
                    "error": (
                        "('Connection aborted.', "
                        "RemoteDisconnected('Remote end closed connection without response'))"
                    ),
                }
            return {
                "succeeded": True,
                "stego_text": "encoded output",
                "angle_index": 7,
                "retry_count": 0,
            }

    class FakeReceiverPipeline:
        pass

    monkeypatch.setattr(module, "StegoPipeline", FakeStegoPipeline)
    monkeypatch.setattr(module, "ReceiverPipeline", FakeReceiverPipeline)
    monkeypatch.setattr(
        module,
        "run_divergence_metrics",
        lambda output_dir, baseline_dir, metrics_dir, progress_hook: {
            "report_path": str(metrics_dir / "report.json"),
            "report": {"dataset_summary": {"usable_stego_samples": 1}},
        },
    )
    monkeypatch.setattr(
        module, "get_workflow_encoding_settings", lambda: {"encoding_profile": "security"}
    )
    monkeypatch.setattr(module, "get_workflow_encoding_secret", lambda: "secret")
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module.run_actual_workload_e2e(
        profiles=["security"],
        samples_per_profile=1,
        post_ids=["post-1"],
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        run_dir=run_root / "run",
        overwrite=False,
        max_retries=1,
        force_model_generation=True,
        skip_receiver_decode=True,
        allow_post_reuse=False,
        fail_fast=True,
        max_transient_sample_retries=1,
        transient_sample_retry_base_delay_seconds=0.0,
    )

    entry = result["profile_summaries"][0]["entries"][0]
    assert result["total_succeeded_samples"] == 1
    assert result["total_failed_samples"] == 0
    assert calls["encode"] == 2
    assert entry["sample_attempt"] == 2
    assert entry["transient_retry_count"] == 1
    assert not any((run_root / "run" / "security" / "failures").glob("*.json"))


def test_run_actual_workload_e2e_adaptive_feedback_writes_artifacts(monkeypatch) -> None:
    module = _load_runner_module()
    temp_root = Path(__file__).resolve().parents[2] / "metrics" / "test-run-actual-workload-e2e"
    temp_root.mkdir(parents=True, exist_ok=True)
    run_root = temp_root / f"actual-workload-e2e-{uuid4().hex}"
    run_root.mkdir()
    angles_dir = run_root / "angles"
    dataset_dir = run_root / "dataset"
    feedback_dir = run_root / "feedback"
    angles_dir.mkdir()
    dataset_dir.mkdir()

    post_json = (
        '{"id":"post-1","url":"https://example.test/a","selftext":"body",'
        '"search_results":[{"url":"https://example.test/a"}],'
        '"angles":[[{"category":"c","source_quote":"q","tangent":"t"}]],"title":"post"}'
    )
    (angles_dir / "post-1.json").write_text(post_json, encoding="utf-8")
    (dataset_dir / "post-1.json").write_text(post_json, encoding="utf-8")

    calls = {"encode": 0}

    class FakeStegoPipeline:
        def encode(self, *, payload: str, post: dict, tag: str, max_retries: int) -> dict:
            del payload, post, tag, max_retries
            calls["encode"] += 1
            if calls["encode"] == 1:
                return {
                    "succeeded": False,
                    "stego_text": "",
                    "angle_index": 0,
                    "retry_count": 1,
                    "error": "Decoding validation failed",
                    "error_details": {"candidate_results": [{"reason": "weak grounding"}]},
                }
            return {
                "succeeded": True,
                "stego_text": "encoded output",
                "angle_index": 0,
                "retry_count": 0,
            }

    class FakeReceiverPipeline:
        pass

    monkeypatch.setattr(module, "StegoPipeline", FakeStegoPipeline)
    monkeypatch.setattr(module, "ReceiverPipeline", FakeReceiverPipeline)
    monkeypatch.setattr(
        module,
        "run_divergence_metrics",
        lambda output_dir, baseline_dir, metrics_dir, progress_hook: {
            "report_path": str(metrics_dir / "report.json"),
            "report": {"dataset_summary": {"usable_stego_samples": 1}},
        },
    )
    monkeypatch.setattr(
        module, "get_workflow_encoding_settings", lambda: {"encoding_profile": "balanced"}
    )
    monkeypatch.setattr(module, "get_workflow_encoding_secret", lambda: "")

    result = module.run_actual_workload_e2e(
        profiles=["balanced"],
        samples_per_profile=1,
        post_ids=["post-1"],
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        run_dir=run_root / "run",
        overwrite=False,
        max_retries=1,
        force_model_generation=True,
        skip_receiver_decode=True,
        allow_post_reuse=False,
        fail_fast=True,
        max_transient_sample_retries=0,
        transient_sample_retry_base_delay_seconds=0.0,
        feedback_run_dir=feedback_dir,
        adaptive_feedback=True,
        max_adaptive_sample_retries=1,
    )

    entry = result["profile_summaries"][0]["entries"][0]
    assert calls["encode"] == 2
    assert entry["adaptive_retry_count"] == 1
    assert entry["adaptive_actions"][0]["action"] == "increase_candidate_angles"
    assert (feedback_dir / "events.jsonl").is_file()
    assert (feedback_dir / "adaptive_actions.jsonl").is_file()
    assert (feedback_dir / "failure_clusters.json").is_file()
    assert (feedback_dir / "leaderboard.json").is_file()


def test_run_actual_workload_e2e_keeps_dedicated_experiment_artifacts(
    monkeypatch,
) -> None:
    module = _load_runner_module()
    temp_root = (
        Path(__file__).resolve().parents[2]
        / "metrics"
        / "experiments"
        / "naturalness_gate_v1"
        / "test-runs"
        / f"actual-workload-e2e-{uuid4().hex}"
    )
    run_dir = temp_root / "runs" / "test_run"
    angles_dir = temp_root / "fixtures" / "angles"
    dataset_dir = temp_root / "fixtures" / "dataset"
    angles_dir.mkdir(parents=True)
    dataset_dir.mkdir(parents=True)

    post_json = (
        '{"id":"post-1","title":"Starbucks closes hundreds of stores",'
        '"selftext":"Store closures are part of a retail restructuring plan.",'
        '"comments":[{"body":"This sounds like a turnaround problem."}],'
        '"angles":[{"category":"Business","source_quote":"Starbucks is closing hundreds of stores during restructuring.",'
        '"tangent":"Discuss store closures and retail restructuring."}]}'
    )
    (angles_dir / "post-1.json").write_text(post_json, encoding="utf-8")
    (dataset_dir / "post-1.json").write_text(post_json, encoding="utf-8")

    class FakeStegoPipeline:
        def encode(self, *, payload: str, post: dict, tag: str, max_retries: int) -> dict:
            del payload, post, tag, max_retries
            return {
                "succeeded": True,
                "stego_text": "Store closures usually mean the turnaround is still under pressure.",
                "angle_index": 0,
                "retry_count": 0,
            }

    class FakeReceiverPipeline:
        pass

    monkeypatch.setattr(module, "StegoPipeline", FakeStegoPipeline)
    monkeypatch.setattr(module, "ReceiverPipeline", FakeReceiverPipeline)
    monkeypatch.setattr(
        module,
        "run_divergence_metrics",
        lambda output_dir, baseline_dir, metrics_dir, progress_hook: {
            "report_path": str(metrics_dir / "divergence.json"),
            "report": {"dataset_summary": {"usable_stego_samples": 1}},
        },
    )
    monkeypatch.setattr(
        module,
        "run_perplexity_metrics",
        lambda output_dir, metrics_dir, progress_hook: {
            "report_path": str(metrics_dir / "perplexity.json"),
            "report": {"perplexity_summary": {"average_perplexity": 1.0}},
        },
    )
    monkeypatch.setattr(
        module, "get_workflow_encoding_settings", lambda: {"encoding_profile": "balanced"}
    )
    monkeypatch.setattr(module, "get_workflow_encoding_secret", lambda: "")

    result = module.run_actual_workload_e2e(
        profiles=["balanced"],
        variants=["balanced_naturalness_gate"],
        samples_per_profile=1,
        post_ids=["post-1"],
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        run_dir=run_dir,
        overwrite=False,
        max_retries=1,
        force_model_generation=True,
        skip_receiver_decode=True,
        allow_post_reuse=False,
        fail_fast=True,
        max_transient_sample_retries=0,
        transient_sample_retry_base_delay_seconds=0.0,
    )

    lane_dir = run_dir / "balanced_naturalness_gate"
    assert result["run_dir"] == str(run_dir.resolve())
    assert (lane_dir / "output-results").is_dir()
    assert (lane_dir / "metrics").is_dir()
    assert (run_dir / "summary.json").is_file()
    assert not (Path(__file__).resolve().parents[2] / "output-results" / "test_run").exists()
