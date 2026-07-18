# Publication Benchmark — Plan

Successor to the completed `naturalness-overhaul` plan (deleted 2026-07-18; recoverable from
git history — final state committed in `10c854d`).

## Objective

Take the already-implemented publication benchmark framework
(`docs/reports/2026-07-18-publication-benchmark-implementation.md`) from "implementation
complete, live run blocked" to a finished, publication-grade paired comparison of our
selection-channel method against the official ZLG baseline on 100 real-source posts, plus the
multi-post legacy-vs-v1 naturalness comparison that the overhaul left as optional follow-up.

## Inherited results and caveats (must be preserved if cited)

From the finalized one-post legacy/v1 comparison
(`datasets/prep_runs/naturalness_legacy_v1_20260718/summary.json`, SHA-256
`8894abe9b818df07ae5c5ebe65fd4d4791ee4786f78e5149432e3b35401f0d1a`):

- **One independent post only** — no cross-post significance claims.
- **M1**: v1 preferred 8–6 over legacy (descriptive only).
- **M2**: 100% synthetic detection in *both* lanes — a severe shared synthetic-style signal,
  not a lane difference. This is the biggest open research risk.
- **M4**: all 28 scores at the 5/5 rubric ceiling — uninformative.
- **M7**: legacy lane has no tangent-DB report (artifact predates `tangent_db_report`).

## Plan documents

- `execution-plan.md` — phased execution sequence with gates.
- `PROGRESS.md` — running status log; newest entry on top per section. Keep it current.

## Operating rules

- **No chargeable live calls** (generation, search, judge tokens, OpenRouter, paid providers)
  without the user's explicit authorization. Offline/deterministic work proceeds freely.
- The benchmark runner **refuses a dirty working tree** by default; `--allow-dirty` is for
  exploration only, never for the publication run.
- On this Windows machine `uv run pytest` / `uv run pyright` intermittently fail with
  "Failed to canonicalize script path" (exit 1, empty output). Use
  `.\.venv\Scripts\python.exe -m pytest -q` / `-m pyright` instead.
- All repo hard rules apply (forbidden carriers, prompt red line, no fabricated quality
  signals, Pydantic v2, no `print`).
- Never present a mock/chat-completion endpoint as the official ZLG baseline — the baseline
  requires the official codec's token-probability hide/extract behavior.
