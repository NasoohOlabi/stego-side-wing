# Validation after refactors

Use this checklist when touching the areas in the repo cleanup plan. Order matters: fast feedback first.

## Phase 1 (boundaries: `app` ↔ `services` ↔ `workflows`)

- `uv run pytest -q src/tests/test_api_v1_workflow_prompts.py` and a sample of `src/tests/test_api_v1_*.py` (or the full glob if time allows)
- Smoke: import `app.routes.api_v1_routes` and confirm `runner`, `workflow_llm_prompts_path` exist (tests rely on monkeypatch targets)

## Phase 2 (runner / pipelines)

- `uv run pytest -q src/tests/test_workflow_runner.py src/tests/test_runner_live_sim.py`
- Sender/receiver: `uv run pytest -q src/tests/test_stego_codec.py src/tests/test_receiver_pipeline.py src/tests/test_pipeline_stego.py`

## Phase 3 (tooling / ergonomics)

- Full `uv run pytest -q`
- `uv run pyright`

## Integration / services gaps

When changing `src/integrations/*` or untested `services/*` modules, add or extend a focused test rather than relying only on API parity tests.
