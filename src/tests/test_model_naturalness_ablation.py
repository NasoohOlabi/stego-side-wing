import importlib.util
import json
import os
from pathlib import Path


def _load_runner_module():
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / ("run_model_naturalness_ablation.py")
    )
    spec = importlib.util.spec_from_file_location("run_model_naturalness_ablation", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_applied_model_lane_restores_env(monkeypatch) -> None:
    module = _load_runner_module()
    monkeypatch.setenv("WORKFLOW_LLM_BACKEND", "google")
    monkeypatch.setenv("WORKFLOW_LM_STUDIO_MODEL", "original-model")
    lane = module.ModelLane(
        lane_id="lane_001",
        provider="lm_studio",
        model="qwen/qwen3.5-9b",
    )

    with module.applied_model_lane(lane):
        assert os.environ["WORKFLOW_LLM_BACKEND"] == "lm_studio"
        assert os.environ["WORKFLOW_LM_STUDIO_MODEL"] == "qwen/qwen3.5-9b"
        assert os.environ["ANGLES_MODEL"] == "qwen/qwen3.5-9b"

    assert os.environ["WORKFLOW_LLM_BACKEND"] == "google"
    assert os.environ["WORKFLOW_LM_STUDIO_MODEL"] == "original-model"
    assert "ANGLES_MODEL" not in os.environ


def test_default_lm_studio_models_start_with_qwen() -> None:
    module = _load_runner_module()
    assert module.DEFAULT_LM_STUDIO_MODELS[:2] == (
        "qwen/qwen3.5-9b",
        "openai/gpt-oss-20b",
    )


def test_preflight_does_not_auto_add_google_models(monkeypatch) -> None:
    module = _load_runner_module()
    monkeypatch.setattr(module, "list_lm_studio_model_ids", lambda: ["qwen/qwen3.5-9b"])
    monkeypatch.setattr(module, "list_google_model_ids", lambda: ["gemma-test"])

    result = module.preflight_model_lanes(
        lm_studio_models=["qwen/qwen3.5-9b"],
        google_models=[],
    )

    assert [lane.model for lane in result.available_lanes] == ["qwen/qwen3.5-9b"]
    assert result.skipped_lanes == []


def test_load_judge_rating_summary(tmp_path: Path) -> None:
    module = _load_runner_module()
    ratings_path = tmp_path / "ratings.jsonl"
    ratings_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "lane_id": "lane_001",
                        "sample_id": "s1",
                        "naturalness_1_to_5": 4,
                        "context_fit_1_to_5": 5,
                    }
                ),
                json.dumps(
                    {
                        "lane_id": "lane_001",
                        "sample_id": "s2",
                        "rating": {"naturalness_1_to_5": 2, "context_fit_1_to_5": 3},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    summary = module.load_judge_rating_summary(ratings_path)

    assert summary["lane_001"]["judge_naturalness_mean"] == 3.0
    assert summary["lane_001"]["judge_context_fit_mean"] == 4.0
    assert summary["lane_001"]["judge_rated_samples"] == 2


def test_run_model_naturalness_ablation_skips_and_blinds(
    monkeypatch,
    tmp_path: Path,
) -> None:
    module = _load_runner_module()
    angles_dir = tmp_path / "angles"
    dataset_dir = tmp_path / "dataset"
    angles_dir.mkdir()
    dataset_dir.mkdir()
    post = {
        "id": "post-1",
        "title": "Local story",
        "selftext": "Original context",
        "comments": [{"body": "baseline comment"}],
        "angles": [{"source_quote": "quote", "tangent": "tangent", "category": "news"}],
    }
    (angles_dir / "post-1.json").write_text(json.dumps(post), encoding="utf-8")
    (dataset_dir / "post-1.json").write_text(json.dumps(post), encoding="utf-8")

    seen_models: list[str | None] = []

    def fake_run_profile(**kwargs):
        model = os.environ.get("WORKFLOW_LM_STUDIO_MODEL") or os.environ.get(
            "GOOGLE_AI_STUDIO_MODEL"
        )
        seen_models.append(model)
        lane_root = Path(kwargs["run_dir"]) / "balanced"
        output_dir = lane_root / "output-results"
        lane_dataset_dir = lane_root / "dataset"
        output_dir.mkdir(parents=True)
        lane_dataset_dir.mkdir(parents=True)
        (lane_dataset_dir / "post-1.json").write_text(json.dumps(post), encoding="utf-8")
        output_file = output_dir / "post-1_version_balanced_0000.json"
        output_file.write_text(
            json.dumps(
                [
                    {
                        "stegoText": "visible text for judging",
                        "embedding": {
                            "senderAudit": {
                                "llm_timings": [
                                    {
                                        "llm_wall_ms": 111,
                                        "llm_adapter_reported_ms": 17,
                                    },
                                    {
                                        "llm_wall_ms": 222,
                                        "llm_adapter_reported_ms": 19,
                                    },
                                ]
                            }
                        },
                        "post": {},
                    }
                ]
            ),
            encoding="utf-8",
        )
        jsd = 0.1 if model == "gemma-test" else 0.2
        return {
            "variant": "balanced",
            "run_dir": str(lane_root),
            "requested_samples": 1,
            "samples_succeeded": 1,
            "samples_failed": 0,
            "entries": [
                {
                    "post_id": "post-1",
                    "sample_index": 0,
                    "output_file": str(output_file),
                    "elapsed_ms": 123,
                    "retry_count": 0,
                }
            ],
            "metrics_report": {
                "config": {
                    "output_dir": str(output_dir),
                    "dataset_dir": str(lane_dataset_dir),
                }
            },
            "summary_metrics": {
                "quality_metrics": {
                    "matched_post_kl": 1.0,
                    "matched_post_jsd": jsd,
                    "perplexity": 10.0,
                    "receiver_success_rate": 1.0,
                }
            },
        }

    monkeypatch.setattr(module, "list_lm_studio_model_ids", lambda: ["openai/gpt-oss-20b"])
    monkeypatch.setattr(module, "list_google_model_ids", lambda: ["gemma-test", "gemini-test"])
    monkeypatch.setattr(module, "run_profile", fake_run_profile)

    result = module.run_model_naturalness_ablation(
        samples_per_model=1,
        post_ids=["post-1"],
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        run_dir=tmp_path / "ablation",
        overwrite=False,
        max_retries=0,
        skip_receiver_decode=True,
        google_models=["gemma-test"],
    )

    assert seen_models == ["openai/gpt-oss-20b", "gemma-test"]
    skipped = result["preflight"]["skipped_lanes"]
    assert skipped[0]["model"] == "qwen/qwen3.5-9b"
    assert result["leaderboard"]["rows"][0]["model"] == "gemma-test"
    assert result["leaderboard"]["rows"][0]["average_request_elapsed_ms"] == 18.0
    assert result["leaderboard"]["rows"][0]["request_count"] == 2
    assert result["leaderboard"]["rows"][0]["average_sample_elapsed_ms"] == 123.0
    assert result["lane_summaries"][0]["summary"]["timing"]["request_elapsed_ms"] == [17.0, 19.0]

    judge_lines = (
        (tmp_path / "ablation" / "judge_samples.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert len(judge_lines) == 2
    exported = json.loads(judge_lines[0])
    assert exported["lane_id"].startswith("lane_")
    assert "model" not in exported
    assert "openai/gpt-oss-20b" not in judge_lines[0]
    assert "qwen/qwen3.5-9b" not in judge_lines[0]
    assert (tmp_path / "ablation" / "leaderboard.json").is_file()
    assert (tmp_path / "ablation" / "summary.json").is_file()


def test_sort_leaderboard_prefers_failure_rate_without_judge_scores() -> None:
    module = _load_runner_module()

    ranked = module._sort_leaderboard(
        [
            {
                "model": "openai/gpt-oss-20b",
                "failure_rate": 0.88,
                "matched_post_jsd": 0.57,
                "matched_post_kl": 6.7,
                "perplexity": 91.1,
                "samples_succeeded": 3,
            },
            {
                "model": "qwen/qwen3.5-9b",
                "failure_rate": 0.64,
                "matched_post_jsd": 0.58,
                "matched_post_kl": 7.1,
                "perplexity": 99.7,
                "samples_succeeded": 9,
            },
        ]
    )

    assert ranked[0]["model"] == "qwen/qwen3.5-9b"
    assert ranked[1]["model"] == "openai/gpt-oss-20b"
