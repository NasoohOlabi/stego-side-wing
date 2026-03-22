import json

import pytest

from workflows.runner import WorkflowRunner


def test_run_full_pipeline_stops_when_data_load_empty():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    calls = []

    class _DummyResearch:
        def process_post_objects(self, posts, step):
            calls.append(("research_objects", len(posts), step))
            return [{"id": "x"}]

    class _DummyAngles:
        def process_post_objects(self, posts, step):
            calls.append(("angles_objects", len(posts), step))
            return [{"id": "x"}]

    runner.research = _DummyResearch()
    runner.gen_angles = _DummyAngles()
    runner.run_data_load = lambda count: calls.append(("data", count)) or []

    result = runner.run_full_pipeline(start_step="filter-url-unresolved", count=3)

    assert result == []
    assert calls == [("data", 3)]


def test_run_full_pipeline_stops_when_research_empty():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    calls = []

    class _DummyResearch:
        def process_post_objects(self, posts, step):
            calls.append(("research_objects", len(posts), step))
            return []

    class _DummyAngles:
        def process_post_objects(self, posts, step):
            calls.append(("angles_objects", len(posts), step))
            return [{"id": "x"}]

    runner.research = _DummyResearch()
    runner.gen_angles = _DummyAngles()
    runner.run_data_load = lambda count: calls.append(("data", count)) or [{"id": "x"}]

    result = runner.run_full_pipeline(start_step="filter-url-unresolved", count=2)

    assert result == []
    assert calls == [("data", 2), ("research_objects", 1, "filter-researched")]


def test_run_full_pipeline_returns_angles_when_all_steps_succeed():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    angles = [{"id": "done"}]
    calls = []

    class _DummyResearch:
        def process_post_objects(self, posts, step):
            calls.append(("research_objects", len(posts), step))
            return [{"id": "x"}]

    class _DummyAngles:
        def process_post_objects(self, posts, step):
            calls.append(("angles_objects", len(posts), step))
            return angles

    runner.research = _DummyResearch()
    runner.gen_angles = _DummyAngles()
    runner.run_data_load = lambda count: calls.append(("data", count)) or [{"id": "x"}]

    result = runner.run_full_pipeline(start_step="filter-url-unresolved", count=1)

    assert result == angles
    assert calls == [
        ("data", 1),
        ("research_objects", 1, "filter-researched"),
        ("angles_objects", 1, "angles-step"),
    ]


def test_run_full_pipeline_skips_data_load_for_non_initial_step():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    calls = []

    class _DummyAngles:
        def process_post_objects(self, posts, step):
            calls.append(("angles_objects", len(posts), step))
            return [{"id": "x"}]

    runner.gen_angles = _DummyAngles()
    runner.run_research = lambda count: calls.append(("research", count)) or [{"id": "x"}]

    result = runner.run_full_pipeline(start_step="filter-researched", count=4)

    assert result == [{"id": "x"}]
    assert calls == [("research", 4), ("angles_objects", 1, "angles-step")]


def test_run_full_pipeline_starting_at_angles_step_runs_only_angles():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    calls = []
    runner.run_gen_angles = lambda count: calls.append(("angles", count)) or [{"id": "x"}]
    result = runner.run_full_pipeline(start_step="angles-step", count=2)
    assert result == [{"id": "x"}]
    assert calls == [("angles", 2)]


def test_run_stego_run_all_stops_when_no_unprocessed_posts():
    runner = WorkflowRunner.__new__(WorkflowRunner)

    class _DummyStego:
        def __init__(self):
            self.calls = 0

        def process_post(self, post_id=None, payload=None, tag=None, list_offset=1):
            self.calls += 1
            if self.calls == 1:
                return {"succeeded": True, "retry_count": 0, "post": {"id": "p1"}}
            if self.calls == 2:
                return {"succeeded": True, "retry_count": 1, "post": {"id": "p2"}}
            raise ValueError("No unprocessed posts found for step='final-step' and tag='manual'.")

    runner.stego = _DummyStego()
    result = runner.run_stego(run_all=True, payload="hello", tag="manual")

    assert result["run_all"] is True
    assert result["processed_count"] == 2
    assert result["succeeded_count"] == 2
    assert result["failed_count"] == 0
    assert result["stopped_reason"] == "no_unprocessed_posts"
    assert len(result["results"]) == 2


def test_run_stego_run_all_max_posts_zero_is_unlimited():
    runner = WorkflowRunner.__new__(WorkflowRunner)

    class _DummyStego:
        def __init__(self):
            self.calls = 0

        def process_post(self, post_id=None, payload=None, tag=None, list_offset=1):
            self.calls += 1
            if self.calls <= 3:
                return {
                    "succeeded": True,
                    "retry_count": 0,
                    "post": {"id": f"p{self.calls}"},
                }
            raise ValueError("No unprocessed posts found for step='final-step' and tag='manual'.")

    runner.stego = _DummyStego()
    result = runner.run_stego(
        run_all=True, payload="hello", tag="manual", max_posts=0
    )

    assert result["processed_count"] == 3
    assert result["max_posts"] is None
    assert result["stopped_reason"] == "no_unprocessed_posts"


def test_run_stego_run_all_stops_on_repeat_failed_post():
    runner = WorkflowRunner.__new__(WorkflowRunner)

    class _DummyStego:
        def process_post(self, post_id=None, payload=None, tag=None, list_offset=1):
            return {"succeeded": False, "retry_count": 4, "post": {"id": "p1"}}

    runner.stego = _DummyStego()
    result = runner.run_stego(run_all=True, payload="hello", tag="manual")

    assert result["run_all"] is True
    assert result["processed_count"] == 2
    assert result["succeeded_count"] == 0
    assert result["failed_count"] == 2
    assert result["stopped_reason"] == "repeat_failed_post"


def test_run_stego_run_all_max_posts_one_caps_batch():
    runner = WorkflowRunner.__new__(WorkflowRunner)

    class _DummyStego:
        def __init__(self):
            self.calls = 0

        def process_post(self, post_id=None, payload=None, tag=None, list_offset=1):
            self.calls += 1
            return {
                "succeeded": True,
                "retry_count": 0,
                "post": {"id": f"p{self.calls}"},
            }

    runner.stego = _DummyStego()
    result = runner.run_stego(
        run_all=True, payload="hello", tag="manual", max_posts=1
    )

    assert result["processed_count"] == 1
    assert result["max_posts"] == 1
    assert result["stopped_reason"] == "max_posts_reached"
    assert runner.stego.calls == 1


class _DummyConfig:
    def __init__(self, root):
        self._step_dirs = {}
        for step in ("filter-url-unresolved", "filter-researched", "angles-step"):
            src = root / f"src_{step}"
            dest = root / f"dest_{step}"
            src.mkdir(parents=True, exist_ok=True)
            dest.mkdir(parents=True, exist_ok=True)
            self._step_dirs[step] = (src, dest)

    def get_step_dirs(self, step):
        return self._step_dirs[step]


class _DummyBackend:
    def __init__(self, config):
        self.config = config


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def test_validate_post_pipeline_success(tmp_path):
    runner = WorkflowRunner.__new__(WorkflowRunner)
    config = _DummyConfig(tmp_path)
    runner.backend = _DummyBackend(config)
    post_id = "p1"

    baseline_data_load = {"id": post_id, "selftext": "x"}
    baseline_research = {"id": post_id, "search_results": ["a", "b"]}
    baseline_angles = {"id": post_id, "angles": [{"source_quote": "a", "tangent": "b", "category": "c"}]}

    _write_json(config.get_step_dirs("filter-url-unresolved")[1] / f"{post_id}.json", baseline_data_load)
    _write_json(config.get_step_dirs("filter-researched")[1] / f"{post_id}.json", baseline_research)
    _write_json(config.get_step_dirs("angles-step")[1] / f"{post_id}.json", baseline_angles)

    runner.preview_data_load_post = lambda post_id, use_cache=False: {
        "post": baseline_data_load,
        "report": {"fetch_success": True},
    }
    runner.preview_research_post = lambda post_id, source_post=None, **kwargs: {
        "post": baseline_research,
        "report": {},
    }
    runner.preview_gen_angles_post = lambda post_id, source_post=None, **kwargs: {
        "post": baseline_angles,
        "report": {},
    }

    result = runner.validate_post_pipeline(post_id)
    assert result["valid"] is True
    assert result["validation_outcome"] == "protocol_match"
    assert result["steps"]["data_load"]["matches"] is True
    assert result["steps"]["research"]["matches"] is True
    assert result["steps"]["gen_angles"]["matches"] is True
    assert result["steps"]["data_load"]["comparison"] == "match"


def test_validate_post_pipeline_reports_mismatch(tmp_path):
    runner = WorkflowRunner.__new__(WorkflowRunner)
    config = _DummyConfig(tmp_path)
    runner.backend = _DummyBackend(config)
    post_id = "p2"

    baseline_data_load = {"id": post_id, "selftext": "x"}
    baseline_research = {"id": post_id, "search_results": ["a", "b"]}
    baseline_angles = {"id": post_id, "angles": [{"source_quote": "a", "tangent": "b", "category": "c"}]}

    _write_json(config.get_step_dirs("filter-url-unresolved")[1] / f"{post_id}.json", baseline_data_load)
    _write_json(config.get_step_dirs("filter-researched")[1] / f"{post_id}.json", baseline_research)
    _write_json(config.get_step_dirs("angles-step")[1] / f"{post_id}.json", baseline_angles)

    runner.preview_data_load_post = lambda post_id, use_cache=False: {
        "post": baseline_data_load,
        "report": {"fetch_success": True},
    }
    runner.preview_research_post = lambda post_id, source_post=None, **kwargs: {
        "post": {"id": post_id, "search_results": ["a", "c"]},
        "report": {},
    }
    runner.preview_gen_angles_post = lambda post_id, source_post=None, **kwargs: {
        "post": baseline_angles,
        "report": {},
    }

    result = runner.validate_post_pipeline(post_id)
    assert result["valid"] is False
    assert result["validation_outcome"] == "protocol_mismatch"
    assert result["steps"]["research"]["matches"] is False
    assert result["steps"]["research"]["comparison"] == "mismatch"
    assert result["steps"]["research"]["changed_keys"]


def test_validate_post_pipeline_restores_original_artifacts(tmp_path):
    runner = WorkflowRunner.__new__(WorkflowRunner)
    config = _DummyConfig(tmp_path)
    runner.backend = _DummyBackend(config)
    post_id = "p2_restore"

    baseline_data_load = {"id": post_id, "selftext": "original"}
    baseline_research = {"id": post_id, "search_results": ["a", "b"]}
    baseline_angles = {
        "id": post_id,
        "angles": [{"source_quote": "a", "tangent": "b", "category": "c"}],
        "options_count": 1,
    }

    data_load_path = config.get_step_dirs("filter-url-unresolved")[1] / f"{post_id}.json"
    research_path = config.get_step_dirs("filter-researched")[1] / f"{post_id}.json"
    angles_path = config.get_step_dirs("angles-step")[1] / f"{post_id}.json"
    _write_json(data_load_path, baseline_data_load)
    _write_json(research_path, baseline_research)
    _write_json(angles_path, baseline_angles)

    runner.preview_data_load_post = lambda post_id, use_cache=False: {
        "post": {"id": post_id, "selftext": "changed"},
        "report": {"fetch_success": True},
    }
    runner.preview_research_post = lambda post_id, source_post=None, **kwargs: {
        "post": {"id": post_id, "search_results": ["changed"]},
        "report": {},
    }
    runner.preview_gen_angles_post = lambda post_id, source_post=None, **kwargs: {
        "post": {
            "id": post_id,
            "angles": [{"source_quote": "x", "tangent": "y", "category": "z"}],
            "options_count": 1,
        },
        "report": {},
    }

    result = runner.validate_post_pipeline(post_id)

    assert result["valid"] is False
    assert json.loads(data_load_path.read_text(encoding="utf-8")) == baseline_data_load
    assert json.loads(research_path.read_text(encoding="utf-8")) == baseline_research
    assert json.loads(angles_path.read_text(encoding="utf-8")) == baseline_angles


def test_validate_post_pipeline_reports_stage_error_without_raising(tmp_path):
    runner = WorkflowRunner.__new__(WorkflowRunner)
    config = _DummyConfig(tmp_path)
    runner.backend = _DummyBackend(config)
    post_id = "p4"

    _write_json(
        config.get_step_dirs("filter-url-unresolved")[1] / f"{post_id}.json",
        {"id": post_id, "selftext": "x"},
    )
    _write_json(
        config.get_step_dirs("filter-researched")[1] / f"{post_id}.json",
        {"id": post_id, "search_results": ["a"]},
    )
    _write_json(
        config.get_step_dirs("angles-step")[1] / f"{post_id}.json",
        {
            "id": post_id,
            "angles": [{"source_quote": "a", "tangent": "b", "category": "c"}],
            "options_count": 1,
        },
    )

    runner.preview_data_load_post = lambda post_id, use_cache=False: {
        "post": {"id": post_id, "selftext": "x"},
        "report": {"fetch_success": True},
    }
    runner.preview_research_post = lambda post_id, source_post=None, **kwargs: (_ for _ in ()).throw(
        RuntimeError("google timed out")
    )
    runner.preview_gen_angles_post = lambda post_id, source_post=None, **kwargs: (_ for _ in ()).throw(
        AssertionError("gen_angles should be skipped after research failure")
    )

    result = runner.validate_post_pipeline(post_id)

    assert result["valid"] is False
    assert result["validation_outcome"] == "rerun_incomplete"
    assert result["steps"]["data_load"]["matches"] is True
    assert result["steps"]["research"]["matches"] is None
    assert result["steps"]["research"]["comparison"] == "rerun_failed"
    assert result["steps"]["research"]["error"] == "google timed out"
    assert result["steps"]["gen_angles"]["matches"] is None
    assert result["steps"]["gen_angles"]["comparison"] == "skipped"
    assert "Skipped because an upstream stage failed" in result["steps"]["gen_angles"]["error"]


def test_validate_post_pipeline_missing_baseline(tmp_path):
    runner = WorkflowRunner.__new__(WorkflowRunner)
    config = _DummyConfig(tmp_path)
    runner.backend = _DummyBackend(config)
    post_id = "p3"

    _write_json(config.get_step_dirs("filter-url-unresolved")[1] / f"{post_id}.json", {"id": post_id})
    _write_json(config.get_step_dirs("filter-researched")[1] / f"{post_id}.json", {"id": post_id})
    # Intentionally omit angles baseline.

    runner.preview_data_load_post = lambda post_id, use_cache=False: {
        "post": {"id": post_id},
        "report": {"fetch_success": True},
    }
    runner.preview_research_post = lambda post_id, source_post=None, **kwargs: {
        "post": {"id": post_id},
        "report": {},
    }
    runner.preview_gen_angles_post = lambda post_id, source_post=None, **kwargs: {
        "post": {"id": post_id},
        "report": {},
    }

    with pytest.raises(FileNotFoundError):
        runner.validate_post_pipeline(post_id)
