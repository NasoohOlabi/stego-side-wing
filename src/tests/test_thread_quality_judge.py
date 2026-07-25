import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_thread_quality_judge.py"
    spec = importlib.util.spec_from_file_location("thread_quality_judge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_tasks_are_metric_model_and_prompt_specific() -> None:
    build_tasks = _load().build_tasks
    rows = [
        {
            "pair_id": "p1",
            "post_id": "post",
            "method": "zlg",
            "post_text": "ctx",
            "stegotext": "reply",
        }
    ]
    first = build_tasks(rows, "thread_relevance", "hash", "model")[0]
    assert first["thread_context"] == "ctx"
    assert first["candidate"] == "reply"
    assert first["task_id"] != build_tasks(rows, "writing_quality", "hash", "model")[0]["task_id"]


def test_parse_result_accepts_only_integer_scale() -> None:
    parse_result = _load().parse_result
    assert parse_result('{"score": 4, "rationale": "clear"}') == {"score": 4, "rationale": "clear"}
    assert parse_result('{"score": 6}')["score"] is None
    assert parse_result('{"score": 3.5}')["score"] is None
    assert parse_result("not json")["score"] is None
