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


def test_run_actual_workload_e2e_retries_transient_sample_failure(
    monkeypatch
) -> None:
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
