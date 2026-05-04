# Validation After Refactors

Use this checklist when touching areas in the repo cleanup plan. Order matters: fast feedback first.

## Phase 1: Boundaries

- Scope: `app` to `services` to `workflows`.
- Run `uv run pytest -q src/tests/test_api_v1_workflow_prompts.py` and a sample of `src/tests/test_api_v1_*.py`.
- If time allows, run the full API test glob.
- Smoke import `app.routes.api_v1_routes` and confirm `runner` and `workflow_llm_prompts_path` exist because tests rely on monkeypatch targets.
- Workflow/system LLM prompt text changes require double confirmation from the project owner before editing `config/workflow_llm_prompts.json`, `src/workflows/utils/workflow_llm_prompts.py`, or related prompt files.

## Phase 2: Runner And Pipelines

- Run `uv run pytest -q src/tests/test_workflow_runner.py src/tests/test_runner_live_sim.py`.
- For sender/receiver changes, run `uv run pytest -q src/tests/test_stego_codec.py src/tests/test_receiver_pipeline.py src/tests/test_pipeline_stego.py`.
- Sender output must be ordinary visible text. Do not use zero-width, invisible, control-format, homoglyph, or other non-rendering Unicode characters as payload carriers.
- Generated stego text must not include explicit ratings, quality scores, or quality opinions unless the source thread naturally asks for one.

## Phase 3: Tooling And Ergonomics

- Run full `uv run pytest -q`.
- Run `uv run pyright`.

## Benchmark Runs

For Pareto search and sample-generation workflows, use [workload-runs-and-artifacts.md](../results/workload-runs-and-artifacts.md).

## Integration And Service Gaps

When changing `src/integrations/*` or untested `services/*` modules, add or extend a focused test rather than relying only on API parity tests.
