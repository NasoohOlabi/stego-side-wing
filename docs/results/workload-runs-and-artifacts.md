# Workload Runs and Artifacts

This repo keeps validation, Pareto screening, and sample-generation artifacts under `metrics/`.

## Test flow

- Fast feedback: run targeted pytest for the files you changed.
- Non-trivial changes: finish with `uv run pytest -q` and `uv run pyright`.
- Sender/receiver and codec changes should prioritize `src/tests/test_stego_codec.py`, `src/tests/test_receiver_pipeline.py`, `src/tests/test_pipeline_stego.py`, and matching `src/tests/test_api_v1_*` coverage.

## Pareto search

- Command: `uv run python scripts/run_pareto_search.py`
- Stage order: synthetic screening first, then the real screen.
- Synthetic screens write to `metrics/pareto_runs/pareto_<timestamp>/synthetic_<payload>b/`.
- Real-screen results write to `metrics/pareto_runs/pareto_<timestamp>/real_screen/`.
- Each Pareto run also writes:
  - `summary.json`
  - `leaderboard.json`
  - `frontier.json`
  - `latest_heartbeat.json`

## Sample generation

- Synthetic sample runs: `uv run python scripts/run_encoding_config_e2e.py`
- Real sample runs: `uv run python scripts/run_actual_workload_e2e.py`
- Synthetic runs write per profile under `metrics/e2e_runs/encoding_profiles_<timestamp>/`.
- Real runs write per profile under `metrics/e2e_runs/actual_workload_e2e_<timestamp>/`.
- Typical per-profile folders:
  - Synthetic: `dataset/`, `output-results/`, `metrics/`, `summary.json`
  - Real: `input-angles/`, `dataset/`, `output-results/`, `failures/`, `metrics/`, `summary.json`
- The latest real-workload summary is also copied to `metrics/e2e_runs/latest_actual_workload_e2e.json`.

## Storage map

- `metrics/e2e_runs/` - sample runs, profile summaries, and per-run summaries.
- `metrics/pareto_runs/` - Pareto screening runs and frontier rollups.
- `metrics/automation_logs/` - stdout, stderr, and progress logs captured by long-running automation wrappers.
- `metrics/*.json` - shared metric reports such as perplexity and divergence outputs.
- `docs/results/sample-size-plan.md` - sample-size policy and benchmark guidance.
- `docs/results/results-findings.md` - interpretation of saved metric artifacts.

