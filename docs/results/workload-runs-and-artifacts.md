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

### Legacy/refactor artifact separation

- Historical angle posts have no `angle_artifact` field. Treat them as
  `legacy_unversioned`; they remain readable and must not be rewritten merely to add
  metadata.
- Newly generated angle posts carry `angle_artifact.schema_version = 2`,
  namespace `selection_channel_angles/refactor_v2`, generator version, sampler version,
  dictionary ID, effective capacity profile/limits, and retained/raw target metadata.
- Isolated prep runs write a schema-v2 `prep_run.json` with the same namespace plus the
  complete effective capacity settings. Schema-v1 manifests remain readable as legacy
  provenance.
- Do not place a refactor prep run in a historical dataset root. The prep-until-quota command
  automatically creates a unique `datasets/prep_runs/refactor_v2/<timestamp>_<tag>` root
  when `--dataset-root` is omitted; an explicit root must likewise be new and isolated.
  Downstream aggregation groups by the complete angle-artifact identity and rejects a mixed
  legacy/refactor or mixed-profile input set unless the comparison uses separate lanes.

Example bounded prep run:

```powershell
$env:WORKFLOW_ENCODING_PROFILE = 'balanced'
$env:WORKFLOW_CAPACITY_LIMITS_ENABLED = '1'
uv run python scripts/run_prep_until_google_quota_then_stego.py `
  --tag bounded_v2 `
  --dataset-root datasets/prep_runs/refactor_v2_20260728 `
  --prep-run-id refactor_v2_20260728
```

### Real-workload smoke command

Run this first after any backend change:

```powershell
uv run python scripts/run_actual_workload_e2e.py `
  --variant security_legacy `
  --samples-per-profile 5 `
  --max-retries 1 `
  --log-level INFO
```

### Pilot command (security shortlist)

```powershell
uv run python scripts/run_actual_workload_e2e.py `
  --variant security_legacy `
  --variant sec_v2_anchored `
  --variant sec_v2_natural_then_anchor_retry `
  --samples-per-profile 25 `
  --max-retries 1 `
  --log-level INFO
```

### Resume policy

- If failures are infra-class (`404`, auth, timeout), fix backend then rerun.
- Keep post IDs fixed for cross-variant comparability.
- Use `metrics/e2e_runs/latest_actual_workload_e2e.json` as the first status check after each run.

## Multi-frame stego benchmark

- Command: `uv run python scripts/run_multi_frame_stego_e2e.py`
- Minimal local harness for encoding a payload across multiple frames per post (`StegoPipeline.encode_payload_frames`) and decoding it back (`ReceiverPipeline.run_multi_frame`). The PoC sends only Elias-gamma frame-count and payload-bit-length fields once at the start; all following bits are payload, with zero padding only in the final carrier.
- The receiver is intentionally order-driven: it uses the sender-produced `ordered_frame_refs` list unchanged and does not scan or timestamp-sort comments.
- Flags: `--mode synthetic|saved-posts` (default `synthetic`; `saved-posts` requires `--post-file <path>`), `--payload-bits` (default `256`), `--max-frames-per-post` (default `3`), `--samples` (parsed but currently unused — each invocation runs once).
- Writes `{encoded, decoded}` to `metrics/e2e_runs/multi_frame_<timestamp>/result.json`.

Example:

```powershell
uv run python scripts/run_multi_frame_stego_e2e.py --mode synthetic --max-frames-per-post 3
```

## Storage map

- `metrics/e2e_runs/` - sample runs, profile summaries, and per-run summaries.
- `metrics/pareto_runs/` - Pareto screening runs and frontier rollups.
- `metrics/automation_logs/` - stdout, stderr, and progress logs captured by long-running automation wrappers.
- `metrics/*.json` - shared metric reports such as perplexity and divergence outputs.
- `docs/results/sample-size-plan.md` - sample-size policy and benchmark guidance.
- `docs/results/results-findings.md` - interpretation of saved metric artifacts.

