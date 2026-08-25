# Current research state — 2026-08-08

This is the authoritative current-state note for active LUCID and ZLG work.
Historical reports remain historical and must not be read as the latest result.

## LUCID refactor phase — closed / code frozen

Project LUCID’s implementation checklist is complete (TangentsDB builder, critic,
revision prompts, honest failure taxonomy, parity checks, dashboard accounting,
pilot scaffold). See
[`docs/plans/project-lucid-tangentdb-and-feedback-loop.md`](../plans/project-lucid-tangentdb-and-feedback-loop.md)
for the full conclusion.

**Freeze:** do not change TangentsDB admission, promising-candidate / sharpen
selection, or workflow LLM prompts based on the first TangentsDB-v1 bulk run.
That run reused a contaminated `datasets/news_researched` cache. Gather enough
clean samples before authorizing any such fix.

## Artifacts

### Sampler baseline (context-weighted v2 — not TangentsDB-v1)

- `metrics/e2e_runs/LUCID_context_weighted_v2_balanced_500`
- **381 / 500** succeeded (76.2% ITT); 64 unique posts with reuse
- Failures mostly `receiver_angle_mismatch`
- Dirty worktree based on `0353848` — not an exact commit pin

### First TangentsDB-v1 bulk attempt (pipeline smoke only)

- Angles: `datasets/prep_runs/LUCID/tangents_db_v1/lucid/news_angles` (197 posts,
  `WORKFLOW_TANGENT_DB_BUILDER=lucid`, `context_weighted_v2` sampler)
- Run: `metrics/e2e_runs/LUCID_tangents_db_v1_balanced_500`
- **371 / 500** succeeded (74.2% ITT); 174 unique success posts; reuse enabled
- Failures: 121 `receiver_angle_mismatch`, 7 `generation_failure`, 1 `stego_invalid_json`
- `search_results` were **not** re-fetched; they match `datasets/news_researched`
  (e.g. post `1lqptry` still carries Kentucky organ-harvest articles on a Texas
  hot-car thread). Contaminated inputs → **not decision-grade** for codebook or
  revision-loop changes.

Traced example (`1lqptry` sample 200): decoder/context gate looked coherent;
selected organ-procurement intent was cache pollution admitted via a thin shared
“under investigation” cue. Documented in the LUCID plan conclusion; no code
change authorized from it.

## ZLG comparison lane

Unchanged relative to the prior note: latest completed recalibrated unpaired ZLG
artifact remains `metrics/zlg_comparison_runs/zlg_batch_scale300_recalibrated`.
Do not treat it as paired with either LUCID 500-run until a frozen, clean,
symmetric manifest exists.

## Next action

1. Keep the LUCID implementation frozen (no codebook/revision/prompt changes from
   contaminated evidence).
2. Grow a **fresh** researched + TangentsDB-v1 angle corpus with the durable
   service documented in
   [`docs/operations/lucid-fresh-research-service.md`](../operations/lucid-fresh-research-service.md)
   (`datasets/prep_runs/LUCID/tangents_db_v1_fresh/`). It runs data-load →
   research → gen-angles only, uses Google then DuckDuckGo/Bing fallbacks, and
   sleeps 24h when search quota is exhausted.
3. Only after enough uncontaminated encode/decode samples exist, reopen failure
   diagnosis (admission vs revision vs prompts) with evidence that is not
   dominated by known cache contamination.
4. Continue writing git commit/branch/dirty, command, model identity, and source
   manifest hash on every workload run.
