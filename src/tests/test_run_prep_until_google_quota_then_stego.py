import importlib.util
from datetime import UTC, datetime
from pathlib import Path


def _load_runner_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "run_prep_until_google_quota_then_stego.py"
    )
    spec = importlib.util.spec_from_file_location(
        "run_prep_until_google_quota_then_stego", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_dataset_root_is_unique_refactor_namespace() -> None:
    module = _load_runner_module()
    created_at = datetime(2026, 7, 28, 12, 34, 56, 123456, tzinfo=UTC)

    root = module._default_dataset_root("pilot / bounded", created_at=created_at)

    assert root == (
        Path(__file__).resolve().parents[2]
        / "datasets"
        / "prep_runs"
        / "refactor_v2"
        / "20260728T123456123456Z_pilot_bounded"
    )
