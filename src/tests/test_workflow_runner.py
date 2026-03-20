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
