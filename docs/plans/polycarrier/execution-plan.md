# POLYCARRIER — Execution plan

Default useful payload: **32 bytes (256 bits)**. Smoke = 5 samples; pilot = 25.

## Prerequisites

```powershell
cd d:/Master/code/stego/stego-side-wing
$env:WORKFLOW_LLM_BACKEND = 'lm_studio'
$env:LM_STUDIO_URL = 'http://127.0.0.1:8081'
$env:WORKFLOW_CONTEXT_SAMPLER = 'context_weighted_v2'
```

| Need | Detail |
|---|---|
| Frozen angles | Schema v3 / `context_weighted_v2` codebooks under an isolated angles dir |
| Metric deps | HF `gpt2`; optional `sacrebleu`, `rouge-score`, `bert-score`+`roberta-large`, `sentence-transformers` `all-MiniLM-L6-v2`; Codex CLI for judges |
| ZLG | local `zero-shot-GLS`: `:8090` llama-server + `:9000` stego API; `/health` OK |

If angles are stale for `context_weighted_v2`, regenerate first:

```powershell
$env:WORKFLOW_DATASET_ROOT = 'D:\Master\code\stego\stego-side-wing\datasets\prep_runs\polycarrier\<tag>'
uv run python scripts/run_context_weighted_angle_batch.py --count 25 --tag polycarrier_<tag>
```

---

## Phase 0 — optional synthetic smoke (ours only)

```powershell
PYTHONPATH=src uv run python scripts/run_multi_frame_stego_e2e.py `
  --mode synthetic --payload-bits 256 --max-frames-per-post 8
```

Gate: planner emits multiple frames; decode recovers the payload.

---

## Phase 1 — ours multi-frame batch

**Capacity note (scale300 angles, 2026-08-06 preflight):** plan defaults
`--posts-per-sample 6 --max-frames-per-post 3` undershoot 256 useful bits + 32-bit
control margin (min batch capacity ≈ 240–270). Use **`6 × 4`** or **`8 × 3`**.

```powershell
# Offline gate (no LLM)
uv run python scripts/polycarrier_capacity_preflight.py `
  --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles `
  --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32

uv run python scripts/run_multi_frame_batch_e2e.py `
  --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles `
  --samples 5 `
  --posts-per-sample 6 `
  --max-frames-per-post 4 `
  --payload-bytes 32 `
  --run-dir metrics/e2e_runs/polycarrier_256b_smoke
```

| Knob | Why |
|---|---|
| `--payload-bytes 32` | Forces multi-frame (and usually multi-post) |
| `--posts-per-sample 6` × `--max-frames-per-post 4` | Fits ≥288-bit need on scale300 (preflight) |

**Artifacts**

- `metrics/e2e_runs/polycarrier_256b_smoke/summary.json`
  - `encode_path: encode_payload_frames`
  - `entries[]` — **one row per frame/comment** (`frame_index`, `embedded_bits`, `output_file`)
  - `records[]` — one row per **sample** (whole payload), with `frame_count`, `decode`
- `output-results/<post_id>_mf_<sample>_<frame>.json`

**Gate**

- `records[].decode.payload_match == true`
- `frame_count > 1` for every succeeded sample
- If planning fails (“does not fit”), raise posts/frames or lower payload bytes

Scale to `--samples 25` → `metrics/e2e_runs/polycarrier_256b_pilot25` after smoke.

---

## Phase 2 — ZLG capacity-matched (per frame)

Unload LM Studio; start ZLG from sibling `D:\Master\code\stego\zero-shot-GLS`
(`start_llama.bat` then `start_stego_api.bat`):

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health

uv run python scripts/run_zlg_batch_comparison.py `
  --source-summary metrics/e2e_runs/polycarrier_256b_smoke/summary.json `
  --server-url http://127.0.0.1:9000 `
  --run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --comparison-mode capacity_matched `
  --zlg-max-new-tokens 640 `
  --max-retries 5
```

Matching: each ours frame’s `commentEmbedding.bitsCount` → UTF-8 payload prefix of
`ceil(bits/8)` bytes. Resume by `source_key`. Record `zero-shot-GLS` Git revision + `/health`.

**Do not** use `max_capacity` in this run-dir for fluency claims.

---

## Phase 3 — Build paired dataset + score metrics

```powershell
uv run python scripts/build_zlg_method_comparison_dataset.py `
  --zlg-run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --source-summary metrics/e2e_runs/polycarrier_256b_smoke/summary.json `
  --dataset-dir <same_angles_dir_as_phase_1> `
  --device auto
```

Outputs: `comparison_dataset/paired_rows.jsonl`, `comparison_dataset/summary.json`.

Optional:

```powershell
# Refresh aggregates only
uv run python scripts/build_zlg_method_comparison_dataset.py `
  --zlg-run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --refresh-statistics-only

uv run python scripts/recompute_paired_divergence_metrics.py `
  --zlg-run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --dataset-dir <angles_dir> --alpha 1e-6

uv run python scripts/recompute_paired_bertscore.py `
  --input metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke/comparison_dataset/paired_rows.jsonl `
  --device cpu --bertscore-model roberta-large

uv run python scripts/run_codex_judge_campaign.py `
  --run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --phase auto --pilot-limit 50 --max-workers 4

uv run python scripts/audit_paired_sample_artifacts.py `
  --run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --source-run-dir metrics/e2e_runs/polycarrier_256b_smoke `
  --output docs/reports/data/polycarrier_256b_smoke_audit.json

uv run python scripts/calibrate_naturalness_gate.py `
  --zlg-run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke
```

Sample-level rollups (bits across frames, pooled PPL, etc.) are defined in
[`metrics-multicomment.md`](metrics-multicomment.md) and computed by:

```powershell
uv run python scripts/aggregate_polycarrier_sample_metrics.py `
  --source-summary metrics/e2e_runs/polycarrier_256b_smoke/summary.json `
  --paired-rows metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke/comparison_dataset/paired_rows.jsonl `
  --dataset-dir <same_angles_dir_as_phase_1> `
  --output metrics/e2e_runs/polycarrier_256b_smoke/sample_layer_metrics.json
```

The stock builder still scores **per frame**; cite sample numbers from
`sample_layer_metrics.json`.

---

## Phase 4 — Dashboard

```powershell
cd d:/Master/code/stego/stego-results-viewer
$env:ZLG_RUN_DIR = 'D:\Master\code\stego\stego-side-wing\metrics\zlg_comparison_runs\zlg_polycarrier_256b_smoke'
pnpm dev
```

Open `/zlg-comparison`. Frame-level panels come from `paired_rows.jsonl`; cite
sample-level numbers from `records[]` + the formulas in `metrics-multicomment.md`.

---

## Run-dir naming

| Stage | Ours | ZLG |
|---|---|---|
| Smoke | `metrics/e2e_runs/polycarrier_256b_smoke` | `metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke` |
| Pilot | `metrics/e2e_runs/polycarrier_256b_pilot25` | `metrics/zlg_comparison_runs/zlg_polycarrier_256b_pilot25` |

Do not merge with unpaired historical artifacts (`LUCID_*_500`,
`zlg_batch_scale300_recalibrated`) for a paired claim.
