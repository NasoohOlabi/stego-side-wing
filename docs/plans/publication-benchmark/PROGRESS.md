# Publication Benchmark — Progress Log

Keep this file current. Newest status at the top of each phase section. Dates are UTC.

## Status dashboard

| Phase | State | Notes |
|---|---|---|
| 0. Persist benchmark workstream | ✅ complete | Focused commits; full quality gate green |
| 1. Live infrastructure | 🔄 in progress | ZLG service deployed; Gemma download running |
| 2. Freeze protocol | 🔄 partial | 46/100 reused offline; 54 live preparations authorized (Stage A) |
| 3. Staged paired run | ✅ authorized, not started | Staged per `live-run-authorization.md` |
| 4. Post-hoc evaluation | ⬜ blocked on 3 | Includes multi-post legacy/v1 lanes |
| 5. Report + paper | ⬜ blocked on 4 | |

## Phase 2 — Freeze the protocol

### 2026-07-18 — Stage A stopped: local LM Studio unavailable

- Started the authorized live preparation after committing the authorization as `2fe3ca1`.
- Search and fetch completed for the first live candidate, but angle generation could not
  proceed because the configured local LM Studio endpoint (`192.168.100.136:1234`) timed out.
- Stopped the preparation process tree. Stage A did not pass; no preflight was run and no
  later stage was started. Resume Stage A only after the frozen local backend is reachable.

### 2026-07-18 — Live-call authorization granted (staged)

- User authorized the remaining chargeable work after independent verification of Phase 0–2
  claims. Scope, constraints, gates, and stop conditions are in
  `live-run-authorization.md` — stages A (54 live preparations + 100-post freeze),
  B (5-post smoke), C (25-post pilot), D (100-post full + max_capacity).
- Key constraints: `WORKFLOW_LLM_BACKEND=lm_studio` stays; switching to a paid per-token
  backend needs new authorization; report search-API usage after Stage A; stop on any
  failed gate.

### 2026-07-18 — Offline preparation exhausted safely

- Hardened malformed historical JSONL handling and made live preparation require `--allow-live`.
- Reused all **46** eligible existing real-source angle artifacts without provider calls.
- Ran the clean-tree preflight twice on the 46-post subset: post hash, deterministic payload
  assignments, and angle hashes reproduced exactly (`post_ids_sha256` starts `e44bb13f`).
- The remaining **54** posts require authorized search and angle-generation calls before the
  100-post manifest can be frozen.

## Phase 1 — Live infrastructure

### 2026-07-18 — Official-codec service implemented and deployed

- Added a versioned FastAPI service around the official EGS hide/extract functions in the
  official baseline repo; local commit and deployed checkout: `ae520f7`.
- Installed official llama.cpp `b10068` beside the remote Qwen3.5-9B GGUF and reused the host's
  CUDA 12 runtime.
- Added deterministic framing, `/health`, `/hide`, `/reveal`, `/capacity_probe`, token-ID replay,
  runtime identity, chat-template handling, sentence completion, and eight offline tests.
- Live local-GPU round trip passed: health operational/model loaded; secret `A` recovered exactly
  from a natural completed sentence. A real `/capacity_probe` trial also passed with exact decode,
  quality acceptance, and framing accounting. Frozen Gemma 3 12B judge/paraphraser download is
  running.

## Phase 0 — Persist the benchmark workstream

### 2026-07-18 — Workstream persisted and validated

- Partitioned and committed provenance, statistical gates, dynamic capacity workflows,
  publication tooling, and CI cleanup in five focused commits (`3b1cacb` through `a8151f5`).
- Removed stale `test_tmp_*` clutter and ignored recurring local pytest scratch paths.
- Full gate passed: **520 passed, 1 skipped**; Pyright **0 errors**; Ruff **clean**;
  `git diff --check` reported no whitespace errors.
- Next action: Phase 1 — implement and deploy the official-codec ZLG service.

### 2026-07-18 — Plan opened after naturalness-overhaul audit

- Audited and closed the naturalness overhaul: final commit `10c854d`, run root
  `datasets/prep_runs/naturalness_legacy_v1_20260718/`, summary SHA-256
  `8894abe9b818df07ae5c5ebe65fd4d4791ee4786f78e5149432e3b35401f0d1a` re-verified.
- Re-ran the full suite via `.venv\Scripts\python.exe -m pytest -q`: **520 passed, 1 skipped,
  0 failed** (an earlier report of 5 failures did not reproduce). Pyright: 0 errors.
  Ruff: clean except 4 pre-existing errors in legacy `extreact_searched_values.py`.
- Deleted the completed `docs/plans/naturalness-overhaul/` folder (history retains it);
  carried its citation caveats into this plan's README.
- Next action: Phase 0 step 1 — partition and commit the benchmark workstream.
