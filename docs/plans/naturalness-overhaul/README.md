# Naturalness Overhaul

**Overhaul folder name:** `naturalness-overhaul`
**Location:** `stego-side-wing/docs/plans/naturalness-overhaul/`
**Goal:** make our zero-shot generative-linguistic-steganography comments read as genuinely
human (kill *topical drift*), do it without clobbering the reproducible corpus, and measure
the result honestly instead of losing on misleading proxies.

This folder is the single source of truth for a three-plan, interdependent effort. Start with
`PROGRESS.md` for current status; read the plans in the order below.

## The three plans (implement in this order)

The user's directive: **one plan at a time, careful about breaking changes**, and stop at the
furthest phase that can be **validated with tests** before touching real outputs / live runs.

1. **[prepared-posts-separate-persistence.md](./prepared-posts-separate-persistence.md)**
   — `WORKFLOW_DATASET_ROOT` rebases every step dir under an isolated tree so a revamped
   corpus never overwrites the reproducible `datasets/news_angles`. Pure path logic; fully
   testable offline. **Prerequisite** for safely A/B-testing plan 2.
2. **[tangent-db-revamp.md](./tangent-db-revamp.md)**
   — new deterministic `tangent_db.py` builder (anchor → distinctness → capacity) that fixes
   drift + near-dupes without changing the sender/receiver bit contract. Gated behind
   `WORKFLOW_TANGENT_DB_BUILDER=legacy|v1`. Builder is pure functions → exhaustively testable.
3. **[method-comparison-metrics-v2.md](./method-comparison-metrics-v2.md)**
   — reframe the ZLG comparison: headline reader-facing metrics (A/B human-likeness +
   sus-detection), split "naturalness" into relevance vs writing-quality, relabel
   perplexity/KL/JSD as topical-fit proxies, add tangent-DB quality metrics, fix the diversity
   artifact. Needs live LLM judge + dataset regeneration → validate deterministic parts
   offline, defer the run.

## Why they're coupled

Revamp (2) needs isolated persistence (1) to A/B safely; metrics (3) measures the revamp's
effect (2) and reads the isolated corpus (1). `tangent_db_report.config_hash` (plan 2) is
carried in `prep_run.json` (plan 1) and asserted by the metrics builder (plan 3).

## Hard rules that constrain all three (from repo CLAUDE.md / AGENTS.md)

- **Prompt red line:** do NOT edit workflow/system generation prompts
  (`config/workflow_llm_prompts.json`, `workflow_llm_prompts.py`) without double confirmation
  or a standalone prompt-only commit. Judge prompts in plan 3 are *evaluation* prompts → not
  under the red line, but keep them versioned/hashed.
- **Receiver contract untouched:** the receiver rebuilds the tangent list from the persisted
  post; DB construction must stay deterministic and reproducible from the post alone.
- **Forbidden carriers / encoding hygiene / no fabricated quality signals** still apply.
- **LLMAdapter only** for workflow + judge LLM calls (no hand-rolled OpenAI clients).
- Pydantic v2, functions ≤25 lines, structured `logger` (no `print`).

## Environment / tooling note (Windows)

`uv run pytest` and `uv run pyright` fail here with `Failed to canonicalize script path` (a
uv-on-Windows quirk). Use **`uv run python -m pytest ...`** and **`uv run python -m pyright ...`**
instead — those work. Run commands from the `stego-side-wing` repo root.
