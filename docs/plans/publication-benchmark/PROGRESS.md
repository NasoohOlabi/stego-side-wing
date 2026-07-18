# Publication Benchmark — Progress Log

Keep this file current. Newest status at the top of each phase section. Dates are UTC.

## Status dashboard

| Phase | State | Notes |
|---|---|---|
| 0. Persist benchmark workstream | ✅ complete | Focused commits; full quality gate green |
| 1. Live infrastructure | 🔄 in progress | Implementing official-codec ZLG service |
| 2. Freeze protocol | ⬜ blocked on 1 | |
| 3. Staged paired run | ⬜ needs authorization | Chargeable live calls |
| 4. Post-hoc evaluation | ⬜ blocked on 3 | Includes multi-post legacy/v1 lanes |
| 5. Report + paper | ⬜ blocked on 4 | |

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
