# Naturalness Overhaul — Progress Log

Keep this file current. Newest status at the top of each plan section. Dates are UTC.

## Status dashboard

| Plan | Phase reached | State | Behavior change? |
|---|---|---|---|
| 1. prepared-posts-separate-persistence | Phase 0 (rebasing core) | ✅ done + validated | No (default-unset = identical) |
| 1. manifest + runner flag | Phase 0 tooling | ✅ done + validated | Opt-in only |
| 2. tangent-db-revamp | Phase 0 (report-only shadow) | ✅ done + validated | No (legacy angles still emitted) |
| 3. method-comparison-metrics-v2 | Phase 1 offline (M5) | ✅ done + validated | Adds deterministic quality scoring |

Operating directive from user: **implement one plan at a time, be careful about breaking
changes/bugs**; stop at the furthest phase validatable by tests before touching real outputs or
requiring a live LLM run. Decision already made: **regenerate the ZLG comparison with distinct
comments per payload before drawing any metric conclusions** (plan 3 M9 is a prerequisite, not
optional).

---

## Plan 1 — prepared-posts-separate-persistence

### 2026-07-18 — Manifest + opt-in runner ergonomics DONE, validated

Added `src/infrastructure/prep_run_manifest.py`, which writes a versioned `prep_run.json`
containing the resolved dataset/step paths, seed corpus, capacity profile, effective tangent-DB
builder, and exact config hash. The prep-until-quota script now accepts `--dataset-root`,
`--prep-run-id`, and `--notes`; default invocation remains unchanged and writes no manifest.

Validation: 8 dataset-root/manifest tests and 96 affected workflow tests passed. Pyright, Ruff,
and `git diff --check` passed. Creating a real isolated corpus remains the live Phase 1 step;
tangent selection is still shadow-only.

### 2026-07-18 — Phase 0 DONE (rebasing core), validated

**Implemented** in `src/infrastructure/config.py`:
- `get_workflow_dataset_root() -> Path | None` — reads `WORKFLOW_DATASET_ROOT` (absolute or
  repo-relative via `resolve_path`); `None` when unset/blank.
- `get_workflow_dataset_seed_global() -> bool` — reads `WORKFLOW_DATASET_SEED_GLOBAL`
  (default `true`).
- `_rebase_step_dir(rel_dir, root, *, seed_global)` — maps a relative step dir under `root` by
  **leaf name**; keeps the `news_cleaned` seed corpus global when `seed_global`.
- Rewrote `get_step_dirs(step)` to rebase source+dest under the root when set. Chose **Option A**
  from the plan (rebase inside `get_step_dirs`) so `WorkflowConfig`, `LocalBackendClient`
  (`get_post_local`/`save_post_local`/`save_object_local`), `process_post_objects`, and
  `StegoPipeline.process_post` inherit isolation with no further threading.

**Leak check:** grepped `news_angles|news_researched|news_url_fetched|output-results|news_cleaned`
across `src`. Only non-test literals that bypass `get_step_dirs` are unrelated to the corpus
pipeline (viewer scan roots in `recent_updates_service.py`, the ad-hoc `/posts` save in
`app/routes/posts_routes.py`, and the `output_dir` request param in `http_parsers.py` /
`routes_tools.py`). No partial-isolation leak in the prepare→stego→receiver path.

**Tests** — new `src/tests/test_dataset_root.py` (6, all pass):
- unset root ⇒ byte-identical to global defaults (all 4 steps)
- root rebases every step by leaf name
- seed corpus stays global by default; its dest is still rebased
- seed rebases when `WORKFLOW_DATASET_SEED_GLOBAL=false`
- absolute root honored
- blank/whitespace root falls back to global

**Validation run:**
- `uv run python -m pytest -q src/tests/test_dataset_root.py` → 6 passed.
- Regression: `test_workflow_runner`, `test_backend_api_adapter`, `test_pipeline_gen_angles`,
  `test_pipeline_stego`, `test_receiver_pipeline` → all pass.
- `uv run python -m pyright src/infrastructure/config.py` → 0 errors.

**Deferred to Phase 1** (nothing to validate offline until we do a real corpus prep):
- `prep_run.json` manifest writer (ties `tangent_db_config_hash` from plan 2 — doesn't exist yet).
- `--dataset-root` / `--prep-run <id>` flag on the prep/orchestration scripts under `scripts/`
  that drive `run_data_load → run_research → run_gen_angles → run_stego`.
- Optional: add `datasets/prep_runs` to the viewer scan roots
  (`stego-results-viewer/src/server/paths.ts` `GENERATED_SCAN_ROOTS`).

**Files touched:** `src/infrastructure/config.py` (edit), `src/tests/test_dataset_root.py` (new).

---

## Plan 2 — tangent-db-revamp

### 2026-07-18 — Phase 0 DONE (report-only shadow), validated

**Implemented:**
- Added pure Pydantic-v2 `src/workflows/utils/tangent_db.py`: deterministic relevance scoring,
  source-weighted admission, lexical Jaccard deduplication, optional similarity relaxation for
  a capacity floor, stable ordering, config hashing, and `tangent_db_report` generation.
- Added the `WORKFLOW_TANGENT_DB_*` config surface and included effective values under
  `get_workflow_capacity_settings()["tangent_db"]`; the default builder remains `legacy`.
- Wired `WORKFLOW_TANGENT_DB_BUILDER=v1` into every angle-generation path as **shadow only**:
  it logs and persists `tangent_db_report`, while legacy capping/gating still determines emitted
  `angles`. Default-unset output remains unchanged. Codec and receiver logic were not touched.
- Published the naturalness gate's deterministic in-thread context accessor for builder reuse.

**Tests:**
- New `src/tests/test_tangent_db.py` covers deterministic output/report ordering, relevance and
  search-source thresholds, near-duplicate removal, capacity-floor relaxation without relevance
  relaxation, output capping, stable/sensitive config hashes, and empty input.
- Pipeline coverage proves v1 is shadow-only and persists its report, while legacy adds no field.
- Targeted tangent/codec/gen-angles/receiver/naturalness/stego suite passed (89 tests).
- Full `uv run python -m pytest -q` passed; full `uv run python -m pyright` passed with 0 errors.

**Next:** Phase 1 is the first selection behavior change. Activate relevance filtering only after
preparing a corpus under the isolated dataset root; do not overwrite the reproducible corpus.

## Plan 3 — method-comparison-metrics-v2

### 2026-07-18 — Phase 1 offline M1 scoring DONE, validated

- Added deterministic M1 scoring with ties worth 0.5, post-clustered win rate, bootstrap CI,
  sign test, invalid-response accounting, and judge provenance.
- The scorer can write a standalone result and ingest it under
  `human_likeness_preference` in the comparison `summary.json`.
- No live judge calls or dataset regeneration were performed.

Validation: M1 runner/scorer tests passed; Ruff, Pyright, and `git diff --check` passed.

**Next:** expose M1 and sus-detection as the viewer headline, including clustered inference and
provenance. Then proceed to offline M3/M4 judge scaffolding.

### 2026-07-18 — Phase 1 offline M1 judge runner DONE, validated

- Added deterministic, position-randomized pairwise human-likeness tasks and a cached JSONL
  runner using `LLMAdapter`.
- Task identity includes prompt hash and judge model; results retain raw response, rationale,
  blinded order, provider/model, temperature, and prompt hash.
- Added a versioned evaluation prompt. No workflow-generation prompt changed and no live judge
  calls were made.

Validation: targeted tests passed; Ruff, Pyright, and `git diff --check` passed.

**Next:** add deterministic post-clustered M1 scoring/summary ingestion, then expose it in the
comparison summary. Do not run the live judge or regenerate outputs automatically.

### 2026-07-18 — Phase 1 offline M5 DONE, validated

- Added a versioned, deterministic 0–100 `lexical_quality_index` to every comparison row.
  Its recorded formula combines lexical diversity, inverse repetition, bigram non-repetition,
  and a small 5–120-word length-sanity component; higher is better.
- Added the index to per-method summaries, row-level paired statistics, and the primary
  post-clustered inference with bootstrap confidence intervals and sign tests.
- Persisted the index version, direction, range, and weights in `summary.json` so results remain
  interpretable if the formula evolves.
- Added regression coverage for score bounds, empty text, repetition sensitivity, versioning,
  and post-clustered statistics.

Validation: full pytest passed with one existing skip; Pyright reported 0 errors/warnings; Ruff
and `git diff --check` passed (line-ending warnings only).

**Next:** Phase 1 M1 A/B human-likeness judge scaffolding via `LLMAdapter` with randomized order,
prompt hash, model provenance, caching, and post-clustered reporting. Do not run the live judge or
regenerate outputs automatically.

### 2026-07-18 — Phase 0 DONE (M9 diversity + M10 post clustering), validated

- Corrected primary inference to aggregate by `post_id`; `sample_index` is no longer treated as
  an independent cluster. Row-level paired statistics remain secondary/descriptive.
- Added a pre-write diversity guard for our-method comments, normalized for whitespace, with a
  configurable `--minimum-diversity-ratio` (default `1.0`). Repeated comments now fail dataset
  builds and statistics-only refreshes instead of producing publishable summaries.
- Summaries persist the threshold, per-post ratios, failing post IDs, and pass status.
- Added regression coverage for cross-sample post clustering, repeated normalized text, and
  acceptance/reporting of distinct comments.

Validation: full pytest passed with one existing skip; Pyright reported 0 errors/warnings; Ruff
and `git diff --check` passed (line-ending warnings only).

**Next:** Phase 1 offline work: M5 objective lexical-quality index and its post-clustered
statistics. M1 judge scaffolding can follow, but the judge and regenerated dataset remain
live-run work.

Last. Build the deterministic/testable parts offline (M5 objective lexical index, M9 diversity
guard, M10 cluster-level reporting); wire M1/M3/M4 judge scaffolding via `LLMAdapter`; the
actual dataset regeneration + judge passes are a live run for the user to trigger. Prereq
already decided: regenerate so each payload yields a **distinct** our-method comment (current
`zlg_demo_20260712_v6_200` has 45/47 posts repeating one comment).
Surfaces: `scripts/build_zlg_method_comparison_dataset.py`, `src/services/stego_metrics_service.py`,
viewer `stego-results-viewer/src/app/zlg-comparison/`.
