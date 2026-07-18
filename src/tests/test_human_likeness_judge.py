from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_human_likeness_judge.py"
    spec = importlib.util.spec_from_file_location("human_likeness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tasks_are_deterministic_blinded_and_prompt_versioned() -> None:
    module = _load()
    rows = [
        {"pair_id": 1, "post_id": "p", "method": "our_method", "stegotext": "ours"},
        {"pair_id": 1, "post_id": "p", "method": "zlg", "stegotext": "theirs"},
    ]
    first = module.build_tasks(rows, "hash", "model")
    assert first == module.build_tasks(rows, "hash", "model")
    assert first[0]["task_id"] != module.build_tasks(rows, "new", "model")[0]["task_id"]
    assert {first[0]["candidate_a"], first[0]["candidate_b"]} == {"ours", "theirs"}


def test_result_maps_blinded_position_to_method_and_keeps_ties() -> None:
    module = _load()
    task = {"method_a": "zlg", "method_b": "our_method"}
    assert (
        module.parse_result('{"winner":"B","rationale":"better"}', task)["winning_method"]
        == "our_method"
    )
    assert module.parse_result('{"winner":"tie"}', task)["winning_method"] is None
    assert module.parse_result("not json", task)["winner"] is None
