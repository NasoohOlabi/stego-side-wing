import importlib.util
from pathlib import Path


def _load_runner_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_pareto_search.py"
    spec = importlib.util.spec_from_file_location("run_pareto_search", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summary(variant: str, *, kl: float, expansion: float) -> dict:
    return {
        "profile": "security" if "security" in variant else variant,
        "variant": variant,
        "samples": 2,
        "samples_succeeded": 2,
        "samples_failed": 0,
        "summary_metrics": {
            "quality_metrics": {
                "matched_post_kl": kl,
                "matched_post_jsd": 0.1,
                "receiver_success_rate": 1.0,
            },
            "carrier_metrics": {
                "hidden_expansion_ratio": expansion,
                "standard_fallback_rate": 0.0,
            },
            "selection_metrics": {"unique_selection_signatures": 2},
            "capacity_metrics": {"bps_total": 0.2},
        },
    }


def test_run_pareto_search_writes_rollups(monkeypatch, tmp_path: Path) -> None:
    module = _load_runner_module()
    calls = {"synthetic": 0, "real": 0}

    def fake_synthetic(**kwargs):
        calls["synthetic"] += 1
        assert kwargs["force_extractive_generation"] is True
        return {
            "run_dir": str(kwargs["run_dir"]),
            "summaries": [
                _summary("balanced", kl=2.0, expansion=1.0),
                _summary("security_legacy", kl=3.0, expansion=2.0),
            ],
        }

    def fake_real(**kwargs):
        calls["real"] += 1
        return {
            "run_dir": str(kwargs["run_dir"]),
            "profile_summaries": [
                _summary("balanced", kl=2.0, expansion=1.0),
                _summary("security_legacy", kl=3.0, expansion=2.0),
            ],
        }

    monkeypatch.setattr(module, "run_encoding_config_e2e", fake_synthetic)
    monkeypatch.setattr(module, "run_actual_workload_e2e", fake_real)

    result = module.run_pareto_search(
        variants=["balanced", "security_legacy"],
        run_dir=tmp_path / "pareto",
        synthetic_samples=2,
        synthetic_payload_sizes=[49],
        real_samples=2,
        max_retries=0,
        compute_perplexity=False,
        compute_receiver=True,
        resume=False,
        overwrite=False,
    )

    assert calls == {"synthetic": 1, "real": 1}
    assert len(result["rows"]) == 4
    assert (tmp_path / "pareto" / "leaderboard.json").is_file()
    assert (tmp_path / "pareto" / "frontier.json").is_file()
    assert (tmp_path / "pareto" / "latest_heartbeat.json").is_file()
