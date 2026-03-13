from workflows.runner import WorkflowRunner


def test_run_full_pipeline_stops_when_data_load_empty():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    calls = []

    runner.run_data_load = lambda count: calls.append(("data", count)) or []
    runner.run_research = lambda count: calls.append(("research", count)) or [{"id": "x"}]
    runner.run_gen_angles = lambda count: calls.append(("angles", count)) or [{"id": "x"}]

    result = runner.run_full_pipeline(start_step="filter-url-unresolved", count=3)

    assert result == []
    assert calls == [("data", 3)]


def test_run_full_pipeline_stops_when_research_empty():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    calls = []

    runner.run_data_load = lambda count: calls.append(("data", count)) or [{"id": "x"}]
    runner.run_research = lambda count: calls.append(("research", count)) or []
    runner.run_gen_angles = lambda count: calls.append(("angles", count)) or [{"id": "x"}]

    result = runner.run_full_pipeline(start_step="filter-url-unresolved", count=2)

    assert result == []
    assert calls == [("data", 2), ("research", 2)]


def test_run_full_pipeline_returns_angles_when_all_steps_succeed():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    angles = [{"id": "done"}]
    calls = []

    runner.run_data_load = lambda count: calls.append(("data", count)) or [{"id": "x"}]
    runner.run_research = lambda count: calls.append(("research", count)) or [{"id": "x"}]
    runner.run_gen_angles = lambda count: calls.append(("angles", count)) or angles

    result = runner.run_full_pipeline(start_step="filter-url-unresolved", count=1)

    assert result == angles
    assert calls == [("data", 1), ("research", 1), ("angles", 1)]


def test_run_full_pipeline_skips_data_load_for_non_initial_step():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    calls = []

    runner.run_data_load = lambda count: calls.append(("data", count)) or [{"id": "x"}]
    runner.run_research = lambda count: calls.append(("research", count)) or [{"id": "x"}]
    runner.run_gen_angles = lambda count: calls.append(("angles", count)) or [{"id": "x"}]

    result = runner.run_full_pipeline(start_step="filter-researched", count=4)

    assert result == [{"id": "x"}]
    assert calls == [("research", 4), ("angles", 4)]
