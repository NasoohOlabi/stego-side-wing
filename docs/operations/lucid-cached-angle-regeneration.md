# LUCID cached-angle regeneration

## Purpose

Regenerate angle artifacts from already-researched posts without spending Google
Search quota or altering historical artifacts. The source researched-post JSON is
read-only; every regenerated artifact is written to a new LUCID directory.

## Current cache inventory (2026-08-02)

- `datasets/news_researched`: 197 cached researched posts.
- `datasets/prep_runs/context_weighted_v2/scale300_20260729/news_researched`:
  167 cached researched posts.

Therefore this workspace can currently make at most 197 unique-post LUCID angle
artifacts without new research. A 500-attempt encoding experiment may reuse those
posts, but its analysis must aggregate/cluster by source post ID. It is not a
500-independent-post evaluation.

## Current bulk-run result (2026-08-03)

`metrics/e2e_runs/LUCID_context_weighted_v2_balanced_500` is the current bulk
artifact. It requested 500 balanced-lane samples from 64 reused frozen posts
and stopped blocked at 381 successes and 119 failures (76.2%). The failures are
108 receiver-angle mismatches and 11 generation failures. It is useful for
failure diagnosis, not yet a completed or independently sampled benchmark.

Its progress metadata has blank Git commit and branch fields and marks the
worktree dirty. The nearest committed base before its start is `0353848` on
`fix/zlg-benchmark-overhaul`; that is provenance context, not a precise code
pin. See [the current research-state report](../reports/2026-08-03-current-research-state.md).

## Run process

Choose an empty output directory. Do not copy, move, overwrite, or delete the
source cache.

```powershell
uv run python scripts/run_context_weighted_angle_batch.py `
  --count 197 `
  --tag LUCID `
  --context-sampler context_weighted_v2 `
  --source-researched-dir datasets/news_researched `
  --output-angles-dir datasets/prep_runs/LUCID/context_weighted_v2/news_angles `
  --log-level INFO
```

The CLI validates that source and destination differ. It applies the supplied
paths only to the `angles-step` for that process and selects the sampler from
the explicit flag; no environment-variable path or sampler configuration is
required or mutated. Existing files in the LUCID output are skipped by the
normal workflow queue, so rerunning the same command resumes an interrupted
regeneration.

## Verification

```powershell
(Get-ChildItem datasets/prep_runs/LUCID/news_angles -File -Filter *.json).Count
```

Keep the resulting `news_angles` lane separate from historical lanes when
measuring angle distinctness and encode/decode failures. Record the source path,
output path, sampler/generator versions from `angle_artifact`, model/provider,
and the exact command alongside each test run.

## Sample-generation process

After at least one LUCID angle artifact exists, use the real workload runner with
explicit input and output paths. The following starts one 500-attempt balanced
lane. `--allow-post-reuse` is necessary until 500 unique researched posts exist;
the run must therefore be analysed by source-post cluster.

```powershell
uv run python scripts/run_actual_workload_e2e.py `
  --variant balanced `
  --samples-per-profile 500 `
  --allow-post-reuse `
  --context-sampler context_weighted_v2 `
  --angles-dir datasets/prep_runs/LUCID/context_weighted_v2/news_angles `
  --dataset-dir datasets/news_cleaned `
  --run-dir metrics/e2e_runs/LUCID_context_weighted_v2_balanced_500 `
  --max-retries 1 `
  --log-level INFO
```

The runner writes the samples, failures, and metrics only under `--run-dir`.
It reads the cached LUCID angle directory and the global cleaned-post corpus;
it does not perform Google Search.

LUCID's tagged angle filenames (`<post-id>_LUCID.json`) are resolved back to
their corresponding `<post-id>.json` cleaned-post input by the workload runner.

## Run isolation note (2026-08-02)

The earlier `datasets/prep_runs/LUCID/news_angles` directory contains 64
preserved artifacts created by two different sampler configurations. It is not
deleted or rewritten, but it must not be used as one evaluation lane because
the workload runner correctly rejects mixed artifact identities. All new LUCID
regeneration and sample generation uses
`datasets/prep_runs/LUCID/context_weighted_v2/` instead.
