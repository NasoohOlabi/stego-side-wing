# Plan: Persist Revamped Prepared Posts in a Separate Folder

Status: proposed
Owner: (you)
Related plans: [[tangent-db-revamp]], [[method-comparison-metrics-v2]]

## 1. Why

The tangent-DB revamp ([[tangent-db-revamp]]) changes how prepared posts (posts with
their `angles` / tangent DB) are built. Those revamped posts **must not overwrite**
the existing prepared corpus, because:

- the current `datasets/news_angles` corpus is the input the published ZLG-comparison
  run was built from — clobbering it destroys reproducibility;
- a **receiver decodes against the exact prepared post the sender used** — mixing v1
  and legacy posts in one folder would silently break round-trips (different tangent
  DB → different `idx` → different bits);
- we want to run **A/B** (legacy vs revamped preparation) side by side for the
  metrics work.

So revamped preparations need their own **run-scoped, isolated** folder, selectable
per run, with everything downstream (angles-step → final-step → stego generation →
receiver) reading and writing within that same isolated tree.

## 2. How persistence works today (ground truth)

Step → directory mapping lives in `src/infrastructure/config.py` (`STEPS`, lines
665–682) and is resolved by `get_step_dirs(step)` (lines 691–697):

| step | source_dir | dest_dir |
|---|---|---|
| `filter-url-unresolved` | `datasets/news_cleaned` | `datasets/news_url_fetched` |
| `filter-researched` | `datasets/news_url_fetched` | `datasets/news_researched` |
| `angles-step` | `datasets/news_researched` | `datasets/news_angles` |
| `final-step` | `datasets/news_angles` | `output-results` |

- `src/workflows/config.py::WorkflowConfig.__init__` (lines 39–42) caches these as
  `posts_directory`, `researched_dir`, `angles_dir`, `output_results_dir`, and
  `get_step_dirs()` (line 68) delegates to the global map.
- `src/workflows/adapters/backend_api.py::LocalBackendClient`:
  - `get_post_local(filename, step)` reads from `get_step_dirs(step)[0]` (source).
  - `save_post_local(post, step)` / `save_object_local(data, step, filename)` write to
    `get_step_dirs(step)[1]` (dest), `mkdir(parents=True, exist_ok=True)`.
- `src/workflows/pipelines/gen_angles.py::process_post_objects` (lines 616–664) saves
  each angle-enriched post via `save_object_local(processed, step="angles-step",
  filename=f"{post_id}[_{tag}].json")`. **Note today's only isolation lever is `tag`,
  which changes the filename but keeps everything in the same `news_angles` dir.**

Conclusion: directories are **global constants**, not run-scoped. `tag` namespaces
*files*, not *folders*. That is insufficient — we want folder-level isolation so a
whole prepared corpus (all steps' artifacts) lives together and can be pointed at as
one unit.

## 3. Design

Add a **dataset-root override** that reroutes all step directories under a chosen
base, without touching the relative step layout. Prefer this over adding new named
steps, because it isolates *every* step's artifacts consistently and keeps the
existing step names/semantics.

### 3.1 Dataset root resolution

Introduce a single resolver used by `get_step_dirs`:

- New env var `WORKFLOW_DATASET_ROOT` (absolute or repo-relative). When set,
  `resolve_path()` for step dirs is rebased under it: e.g.
  `WORKFLOW_DATASET_ROOT=datasets/prep_runs/v1_20260718` maps
  `angles-step.dest_dir` → `datasets/prep_runs/v1_20260718/news_angles`,
  `final-step.dest_dir` → `datasets/prep_runs/v1_20260718/output-results`, etc.
- When unset, behavior is **byte-identical** to today (default roots).
- Keep `output-results` addressable: either rebase it under the root (recommended, so
  a prep run is fully self-contained) or keep it global — pick one and document it.
  Recommended: **rebase everything**, so `datasets/prep_runs/<run>/` is a complete,
  copyable unit: `news_url_fetched/`, `news_researched/`, `news_angles/`,
  `output-results/`.

Implementation options (choose A):

- **A. Rebase in `get_step_dirs` (smallest blast radius):** in
  `src/infrastructure/config.py`, have `get_step_dirs(step)` consult a
  `get_workflow_dataset_root()` and, if set, join the *relative* `source_dir`/`dest_dir`
  under the root. `WorkflowConfig` already calls `get_step_dirs`, so it inherits the
  rebase with no further change. `news_cleaned` (the raw seed corpus) should stay
  global unless a run explicitly seeds its own — treat the seed source specially.
- **B. Per-`WorkflowConfig` roots:** pass a `dataset_root` into `WorkflowConfig`.
  More explicit but requires threading through every construction site and the
  `ContextVar`-based config. Heavier; only if we need multiple roots in one process.

### 3.2 Run manifest

Write a `prep_run.json` at the dataset root capturing provenance so a prepared corpus
is self-describing and the receiver/metrics can trust it:

```
{
  "run_id": "v1_20260718T101500Z",
  "tangent_db_builder": "v1",
  "tangent_db_config_hash": "...",
  "capacity_profile": "balanced",
  "created_at_utc": "...",
  "seed_corpus": "datasets/news_cleaned",
  "notes": "relevance-anchored + distinctness dedup"
}
```

This ties directly to `tangent_db_report.config_hash` from [[tangent-db-revamp]] — a
receiver or the metrics builder can assert the prepared corpus matches the DB recipe.

### 3.3 Receiver / downstream

- The receiver already reads the prepared post it is given; it just needs to be
  pointed at the right root. Ensure any receiver entrypoint that loads a post by id
  honors `WORKFLOW_DATASET_ROOT` (it will, if it goes through `get_step_dirs`).
- `StegoPipeline.process_post()` selects posts via `posts_list(step="final-step")`,
  which also flows through `get_step_dirs` → inherits the root. Verify
  `_select_next_post_id` picks from the run's `output-results`/`news_angles`.

## 4. Config surface

| Env var | Meaning | Default |
|---|---|---|
| `WORKFLOW_DATASET_ROOT` | rebase all step dirs under this base | unset (global default dirs) |
| `WORKFLOW_DATASET_SEED_GLOBAL` | keep `news_cleaned` seed global even when root set | `true` |

Echo the resolved roots in run logs (bind `component=`/`trace_id=`) and in the metrics
run summary so every artifact says which corpus it came from.

## 5. Runner / script ergonomics

- Add a `--dataset-root` (or `--prep-run <id>`) flag to the prep/orchestration
  entrypoints (e.g. the scripts under `scripts/` that drive `run_data_load →
  run_research → run_gen_angles → run_stego`) that sets `WORKFLOW_DATASET_ROOT` for
  the child process and writes `prep_run.json`.
- Convention: `datasets/prep_runs/<run_id>/...`. Keep it out of the metrics tree so
  `metrics/` stays for results; prepared corpora live under `datasets/prep_runs/`.
- The results viewer already discovers `output-results` folders under `metrics/*`
  ([[method-comparison-metrics-v2]] references this); if we want prep-run
  `output-results` visible there too, add `datasets/prep_runs` to the viewer's scan
  roots (`stego-results-viewer/src/server/paths.ts` `GENERATED_SCAN_ROOTS`).

## 6. Phased rollout

- **Phase 0:** implement `WORKFLOW_DATASET_ROOT` rebasing + `prep_run.json`; prove
  default-unset path is byte-identical (hash the resolved dirs in a test).
- **Phase 1:** prepare a v1 corpus into `datasets/prep_runs/v1_<ts>/` using the
  tangent-DB v1 builder; keep legacy corpus untouched.
- **Phase 2:** run stego + receiver end-to-end entirely within the v1 root; confirm
  round-trip decode works against the isolated corpus.
- **Phase 3:** build the A/B comparison dataset (legacy vs v1) for metrics-v2.

## 7. Testing

- `get_step_dirs` with `WORKFLOW_DATASET_ROOT` unset returns today's paths exactly
  (guard against regressions).
- With the root set, every step's source/dest resolves under the root and directories
  are created on write.
- Seed-corpus handling: `news_cleaned` stays global when
  `WORKFLOW_DATASET_SEED_GLOBAL=true`.
- End-to-end: prepare → stego → receiver within a temp root recovers the payload
  (round-trip), and reading from the *default* root does **not** see the temp run's
  files (isolation).
- Manifest written with correct `tangent_db_config_hash`.

Run affected suites: `test_workflow_runner.py`, `test_pipeline_gen_angles.py`,
`test_pipeline_stego.py`, `test_receiver_pipeline.py`, plus any test that constructs
`WorkflowConfig` or calls `get_step_dirs`. Then full `uv run pytest -q` + `uv run
pyright`.

## 8. Risks / decisions

- **Path portability:** keep using `resolve_path()` semantics; support both absolute
  and repo-relative roots. Windows paths (this repo runs on Win) — use `pathlib`
  joins only, never string concatenation.
- **Partial isolation bug:** the biggest danger is *one* step still writing to the
  global dir (e.g. a code path that hardcodes a dir instead of `get_step_dirs`). Grep
  for direct `datasets/news_angles` / `output-results` literals and route them all
  through the resolver.
- **Seed corpus:** decide explicitly whether a prep run reuses the global
  `news_cleaned` seed or copies its own. Default: reuse global seed, isolate
  everything downstream.
- **Viewer discovery:** optional; only wire `datasets/prep_runs` into the viewer scan
  roots if you want prep-run outputs browsable there.
