from __future__ import annotations

import importlib.util
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blinded_tasks_are_deterministic_and_keep_answer_separate() -> None:
    module = _load("build_suspiciousness_tasks")
    rows = [
        {
            "post_id": "p1",
            "method": "our_method",
            "accepted": True,
            "stegotexts": ["generated carrier"],
            "human_texts": ["human one", "human two", "human three"],
        }
    ]

    first = module.build(rows, "hash", "judge")
    second = module.build(rows, "hash", "judge")

    assert first == second
    assert "correct_index" not in first[0][0]
    assert first[1][0]["correct_index"] in {0, 1, 2}


def test_judgment_scoring_retains_raw_response() -> None:
    module = _load("score_suspiciousness_judgments")
    keys = [{"task_id": "t", "post_id": "p", "method": "zlg", "correct_index": 2}]
    judgments = [{"task_id": "t", "selected_index": 2, "raw_response": "candidate 3"}]

    rows = module.score(keys, judgments)

    assert rows[0]["valid"] is True
    assert rows[0]["correct"] is True
    assert rows[0]["raw_response"] == "candidate 3"
