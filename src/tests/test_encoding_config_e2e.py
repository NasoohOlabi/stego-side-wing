import importlib.util
from pathlib import Path


def _load_runner():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_encoding_config_e2e.py"
    spec = importlib.util.spec_from_file_location("run_encoding_config_e2e", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_encoding_config_e2e


def test_encoding_config_e2e_runner_small_matrix(
    tmp_path: Path, clear_workflow_capacity_env
) -> None:
    run_encoding_config_e2e = _load_runner()
    result = run_encoding_config_e2e(
        profiles=["robustness", "security"],
        samples_per_profile=3,
        payload_bytes=96,
        run_dir=tmp_path / "profile-e2e",
        overwrite=False,
        seed=42,
        max_primary_kl=1e-12,
    )

    assert result["samples_per_profile"] == 3
    assert [row["profile"] for row in result["summaries"]] == ["robustness", "security"]
    for row in result["summaries"]:
        assert row["unique_payloads"] == 3
        assert row["unique_visible_texts"] == 3
        assert row["metrics_report"]["dataset_summary"]["usable_stego_samples"] == 3
