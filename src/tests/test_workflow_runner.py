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
