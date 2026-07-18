# Naturalness Overhaul — Progress Log

Keep this file current. Newest status at the top of each plan section. Dates are UTC.

## Status dashboard

| Plan | Phase reached | State | Behavior change? |
|---|---|---|---|
| 1. prepared-posts-separate-persistence | Phase 0 (rebasing core) | ✅ done + validated | No (default-unset = identical) |
| 1. manifest + runner flag | Phase 0 tooling | ✅ done + validated | Opt-in only |
| 2. tangent-db-revamp | Phase 1 (v1 selection activated) | ✅ offline validated | Opt-in v1 only |
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

### 2026-07-18 — Phase 1 v1 emitted-angle selection DONE, offline validated

- `WORKFLOW_TANGENT_DB_BUILDER=v1` now emits the deterministic builder's selected angles on
  extractive, analyzed, and fallback generation paths instead of running report-only shadow
  selection. Persisted `angles`, `options_count`, and `tangent_db_report` describe the same DB.
- The unset/default `legacy` path is unchanged and still emits no tangent report.
- Re-running angle generation from the same post/config produces identical ordering and config
  hash for sender/receiver reconstruction.

Validation: targeted tangent/gen-angles/codec/receiver/naturalness/stego tests passed (90).
Full pytest, Pyright, Ruff, and `git diff --check` passed. No provider or corpus write ran.

**Remaining live-run steps/blocker:** populate the initialized isolated legacy/v1 lanes under
`datasets/prep_runs/naturalness_legacy_v1_20260718`; generate one distinct our-method comment
per payload; run M1/M3/M4/M2 judges; build both summaries; run drift attribution; then run
`prepare_tangent_db_comparison.py finalize`. This needs explicit authorization, credentials,
and chargeable generation/search/judge calls. Do not overwrite the reproducible corpus.

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

### 2026-07-18 — Phase 3 M8 offline drift-attribution analyzer DONE, validated

- Added `scripts/analyze_tangent_drift_attribution.py`, which joins cached our-method
  synthetic-detection outcomes to persisted tangent reports by `post_id`.
- It reports detected vs non-detected post-clustered search share and relevance, plus a
  deterministic coarse taxonomy of the detected-case judge reasons. Missing groups remain
  `null`; the analyzer does not invent measurements or call a provider.
- The current reproducible comparison predates tangent reports, so no M8 conclusion was drawn.
  Run the analyzer only after the authorized legacy/v1 population and judge passes:
  `uv run python scripts/analyze_tangent_drift_attribution.py --paired-rows <lane>/paired_rows.jsonl --sus-results <lane>/sus_detection_results.jsonl --output <lane>/tangent_drift_attribution.json`.

Validation: targeted tests passed; Ruff and Pyright passed.

**Remaining blocker:** all deterministic/offline milestones M5–M10 are implemented. Population
of both initialized lanes, distinct-comment generation, and M1/M3/M4/M2 judge passes require
live provider calls/credentials and can incur generation, search, and judge-token charges.
Do not run them without explicit authorization. Then finalize with
`uv run python scripts/prepare_tangent_db_comparison.py finalize --root datasets/prep_runs/naturalness_legacy_v1_20260718 --legacy-summary <legacy-summary.json> --v1-summary <v1-summary.json>`.

### 2026-07-18 — Legacy/v1 offline comparison harness DONE, validated

- Added `scripts/prepare_tangent_db_comparison.py` with an offline `init` command that creates
  separate legacy and v1 dataset roots/manifests without making provider calls.
- Added deterministic `finalize` validation: each manifest must match its lane, each summary
  must retain the M9 `1.0` distinct-comment guard, and both must provide the M7 quality contract.
  It writes the viewer-ready `tangent_db_quality_summaries.legacy` and `.v1` object.
- Initialized `datasets/prep_runs/naturalness_legacy_v1_20260718/`; its comparison contract
  records that no live provider call has started. The generated run root is intentionally ignored.

Validation: 13 targeted tests passed; Ruff, Pyright, and `git diff --check` passed.

**Next/blocker:** populate both initialized lanes and generate distinct comments per payload.
That requires live workflow/provider calls and was not inferred from the broad continuation
request. Once both lane summaries exist, run the harness `finalize` command; it will refuse any
lane that weakens/fails M9 before producing the viewer-ready combined summary.

### 2026-07-18 — Viewer M7 tangent DB comparison panel DONE, validated

- Added a legacy/v1 toggle to the ZLG comparison dashboard, backed by the persisted
  `tangent_db_quality` object whose contract version is `tangent_db_quality_summary_v1`.
- The v1 panel exposes post-clustered kept relevance, pairwise Jaccard, search share, kept
  capacity, deduplication rate, and capacity-floor relaxation outcomes.
- Absent or zero-post sides are explicitly labeled `Not run`; the panel is ready to accept
  separate legacy/v1 summaries after a future comparison. No live comparison was performed.

Validation: targeted Biome and `git diff --check` passed. Repo-wide TypeScript checking remains
blocked only by the previously recorded stego-process/admin and Recharts errors; no diagnostic
was introduced by this change. Viewer commit: `0fb5771`.

**Next:** prepare a new isolated corpus using the Plan 1 dataset-root/manifest tooling, then run
the explicit legacy-vs-v1 tangent DB comparison only when the live run is authorized. Persist
the two summaries under `tangent_db_quality_summaries.legacy` and `.v1` so the completed viewer
toggle can display both. Do not reuse or overwrite the reproducible corpus, and preserve the M9
requirement that each payload produce a distinct our-method comment.

### 2026-07-18 — Phase 3 M7 offline summary contract DONE, validated

- Extended `tangent_db_report.relevance` with the exact kept-score distribution and mean; its
  existing min/median/max/threshold fields remain available.
- Added deterministic `tangent_db_quality_summary_v1` computation to comparison summaries. It
  reports kept relevance, pairwise-Jaccard distinctness, post/comments/search composition and
  search share, near-duplicate removal, kept capacity, and capacity-floor relaxation outcomes.
- Repeated payload rows are collapsed by `post_id`; the contract explicitly records
  `unique_post_id` as its inference unit, stable post ordering, and sorted config hashes.
- New builds retain each our-method row's source `tangent_db_report`; statistics-only refreshes
  recompute M7 without a live model or corpus run. Missing reports yield an explicit zero-post
  summary rather than invented measurements.

Validation: targeted M7/tangent-builder tests passed (19); Ruff passed. Full pytest and full
Pyright passed. No live legacy-vs-v1 comparison was run.

**Next:** expose the completed `tangent_db_quality_summary_v1` contract in the viewer as a
legacy/v1-ready toggle/panel, with unavailable sides labeled `Not run`. Do not run the live
legacy-vs-v1 corpus comparison yet. After the viewer contract, prepare the isolated corpus and
perform the explicit live comparison only when authorized.

### 2026-07-18 — Viewer Phase 2 M6 relabeling DONE, validated

- Relabeled perplexity as a model-predictability/fluency proxy and KL/JSD as
  word-distribution topical-fit proxies across the ZLG dashboard, histogram, metric cards,
  and standalone perplexity/divergence report renderers.
- Every affected surface now explicitly says these metrics do not measure reader-facing
  naturalness or human authorship; the perplexity copy also notes that unusually low values
  can characterize AI text.
- Removed the remaining comparison-summary framing that called the distributional metrics
  naturalness proxies. No metric computation or stored result changed.

Validation: Biome and `git diff --check` passed for all five edited viewer files. Repo-wide
TypeScript checking remains blocked by the previously recorded stego-process/admin and Recharts
formatter errors; no new M6 diagnostic was introduced. Viewer commit: `1f39a14`.

**Next:** begin Phase 3 M7 offline by defining and computing tangent-DB quality summaries from
persisted `tangent_db_report` data (relevance distribution, source composition/search share,
deduplication, and capacity-floor relaxation), with deterministic post-clustered reporting and
tests. Do not run a live legacy-vs-v1 corpus comparison yet; expose the viewer toggle only after
the summary contract exists.

### 2026-07-18 — Viewer M3/M4/M5 two-axis panel DONE, validated

- Added a dedicated dashboard panel that separates M3 thread relevance from M4 judged writing
  quality and M5 deterministic lexical quality.
- M3/M4 read summary-ingested or standalone cached scorer output and report method means,
  post-clustered differences, bootstrap confidence intervals, sign-test p-values, independent
  post counts, and prompt/model provenance. Missing judgments are explicitly labeled `Not run`.
- No live judge calls or output regeneration were performed.

Validation: Biome passed for the edited page and `git diff --check` passed. Repo-wide TypeScript
checking remains blocked by pre-existing stego-process/admin and Recharts type errors; no new
error was reported for the edited page.

**Next:** complete Phase 2 M6 by relabeling perplexity/KL/JSD throughout the viewer as
distributional topical-fit/fluency proxies, with explicit caveats against treating them as
reader-facing naturalness.

### 2026-07-18 — Phase 2 offline M3/M4 scaffolding DONE, validated

- Added one cached `LLMAdapter` runner for M3 thread relevance and M4 writing quality, with
  separate versioned evaluation prompts, stable task IDs, prompt hashes, model/provider
  provenance, raw responses, and strict 1–5 parsing.
- Added standalone/summary-ingesting scoring with post-clustered paired differences, bootstrap
  confidence intervals, sign tests, invalid-response accounting, and row-level method means.
- No live judge calls or output regeneration were performed.

Validation: targeted M1/M3/M4 tests passed; Ruff, Pyright, and `git diff --check` passed.

**Next:** expose the M3 relevance vs M4/M5 writing-quality two-axis panel in the viewer, while
keeping missing live judgments labeled `Not run`.

### 2026-07-18 — Viewer M1/M2 headline DONE, validated

- Promoted reader-facing M1 human-likeness and M2 synthetic-detection metrics above the
  distributional proxy cards in the ZLG comparison dashboard.
- M1 reads either the summary-ingested result or standalone cached scorer output and displays
  the post-clustered score, bootstrap CI, sign-test p-value, independent-post count, and judge
  prompt/model provenance. Missing live judgments are labeled `Not run`.
- M2 is recomputed from cached result rows with posts as the independent unit; the existing
  row-level McNemar result remains explicitly labeled as row-level.

Validation: Biome passed for the edited page and `git diff --check` passed. Repo-wide TypeScript
checking remains blocked by pre-existing errors in stego-process/admin files and the existing
Recharts formatter types; no new error was reported for the edited page.

**Next:** add offline M3 thread-relevance and M4 writing-quality judge scaffolding with cached
results, prompt hashes, model provenance, and post-clustered scoring. Do not run live judges.

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
