# Publication Benchmark — Execution Plan

Each phase ends with a validation gate. Do not start a later phase before the earlier gate
passes. Phases 0 is offline; phases 1+ progressively require infrastructure and authorized
live calls.

## Phase 0 — Persist the benchmark workstream (offline, no charges)

The framework currently exists only as ~55 modified tracked files and ~30 untracked files in
the working tree. This blocks the manifest freeze (the runner refuses a dirty tree) and risks
losing the work.

1. Partition the dirty tree using the "Main files added / Main files modified" lists in
   `docs/reports/2026-07-18-publication-benchmark-implementation.md`.
2. Commit the benchmark workstream as one or more focused commits (codec/pipeline changes,
   benchmark scripts + configs, tests, docs). Review each hunk — the tree mixes concurrent
   workstreams (Luna comparison leftovers, viewer-support edits).
3. Reconcile or intentionally discard the remaining non-benchmark changes; delete stale
   `test_tmp_*` / `*_pytest_tmp*` clutter or extend `.gitignore`.
4. Optional cleanup: fix the 4 pre-existing Ruff errors in `extreact_searched_values.py`.

**Gate:** `git status` clean (or only intentionally-ignored paths); full pytest green via
`.venv\Scripts\python.exe -m pytest -q`; Pyright 0 errors; Ruff clean.

## Phase 1 — Live infrastructure

1. Recover or implement the versioned ZLG service (`/health`, `/hide`, `/reveal`,
   `/capacity_probe`) against the official codec. Live service checkout is sibling
   `D:\Master\code\stego\zero-shot-GLS` (`scripts/stego_api_server.py`; see workspace
   `STEGO_API_SERVER.md`). `tmp_zero_shot_gls_official` remains the vendored codec reference.
2. Confirm the service beside the local Qwen3.5-9B GGUF on this ASUS host
   (`C:\Users\ASUS\.lmstudio\models\lmstudio-community\Qwen3.5-9B-GGUF`); record its commit
   or image digest for `--zlg-server-version`. Use `http://127.0.0.1:9000` from
   `stego-side-wing` runners (no SSH tunnel).
3. Install / make available the frozen Gemma judge+paraphraser model (cross-family from the
   Qwen generator, per `config/benchmark_models.json`).

**Gate:** ZLG `/health` reports operational + loaded model + version; a manual single
hide→reveal round trip succeeds; judge model reachable.

## Phase 2 — Freeze the protocol (cheap, deterministic)

1. `uv run python scripts/prepare_publication_posts.py --count 100` — reuse eligible frozen
   angle artifacts offline first. Add `--allow-live` only after explicit authorization to generate
   missing real-source posts; legacy ZLG demo post IDs remain excluded.
2. `uv run python scripts/benchmark_preflight.py ...` — freeze the manifest (post IDs +
   hashes, model manifest, protocol, deterministic 64-bit payloads).

**Gate:** manifest validates, clean-tree check passes, `benchmark_preflight` reproducible.

## Phase 3 — Staged paired run (authorized live calls)

Requires explicit user authorization before starting (generation + judge + search charges).

1. Five-post infrastructure smoke test.
2. 25-post pilot (`--stage pilot`), inspect retained failures.
3. Expand to 100 (`--stage full` / `auto`) only if the automated gate passes
   (≥80% generation acceptance, ≥95% verified recovery per method).
4. Run the separate `max_capacity` lane with the same ceilings; never mix its claims with
   the capacity-matched lane.

**Gate:** every (post, method) tuple has an attempt row; accounting invariants pass.

## Phase 4 — Post-hoc evaluation on the frozen corpus

1. Passive detector (`analyze_passive_detector.py`, grouped by post ID).
2. Suspiciousness judging (build → judge → score → analyze pipeline, frozen prompt,
   cross-family judge).
3. Robustness attacks (`build_attack_corpus.py` → `run_paraphrase_attacks.py` /
   deterministic attacks → `run_attack_receivers.py` → `analyze_attack_recovery.py`),
   capacity-matched lane only.
4. **Multi-post legacy-vs-v1 naturalness lanes** — the overhaul's optional follow-up.
   Reuse the `prepare_tangent_db_comparison.py` harness across the benchmark's post set to
   replace the one-post result; this is also the venue to attack the shared 100%-M2-detection
   problem with real variation across posts.

**Gate:** each analysis reproducible from cached artifacts without further live calls.

## Phase 5 — Report and dissemination

1. `analyze_publication_results.py` final paired statistical report (post-clustered
   bootstrap, Holm-corrected secondary families, ITT vs successful-output-only kept
   separate).
2. Surface results in `stego-results-viewer` (panels already support `Not run` states).
3. Write up in `project_paper` with all inherited caveats preserved.
