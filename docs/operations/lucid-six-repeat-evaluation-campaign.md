# LUCID fresh six-repeat evaluation campaign

Status date: 2026-08-15

## Scope

The continuous fetch/research/angle preparation campaign was intentionally
paused before this campaign began. Its login-start supervisor was removed and
the three preparation workers were stopped. The source tree is
`datasets/prep_runs/LUCID/tangents_db_v1_fresh`.

The frozen evaluation manifest contains **674 eligible posts** and plans
**4,044 embedding attempts**: six independent payload attempts per post. A post
is eligible only when its fetched artifact exists, its research is non-empty,
and its TangentsDB-v1 artifact parses and reports exactly 32 retained angles.
The freeze excluded 78 incomplete angle artifacts and 20 empty research
artifacts. SHA-256 hashes for all three source artifacts are retained per post.

Artifacts are under:

`metrics/evaluation_campaigns/lucid_fresh_6x_20260815`

## Execution and gates

The campaign is deliberately batched by 25 independent posts. Each batch asks
`run_actual_workload_e2e.py` for 150 attempts, cycling its 25-post list exactly
six times. A completed batch is never overwritten; an incomplete directory is
retained for inspection. The first 25 posts are the pilot gate. Full execution
starts only after generation, recovery, failure-rate, and metric outputs from
that pilot have been reviewed.

Start or resume the pilot:

```powershell
uv run python scripts/run_lucid_evaluation_campaign.py --stage pilot
```

After the pilot passes, start or resume the full generation phase:

```powershell
uv run python scripts/run_lucid_evaluation_campaign.py --stage full
```

The runner verifies every frozen input hash before starting. LM Studio at
`http://127.0.0.1:8081` supplies `qwen/qwen3.5-9b`. The campaign uses the
`balanced` variant, `context_weighted_v2`, real receiver decoding, and one
encode retry.

## Evaluation coverage

Every successful embedding records payload size/hash, capacity and effective
recovered-bit metrics, exact receiver recovery, timing, retries, angle choice,
and the sample-level experiment metrics. At the end of each batch the existing
runner computes perplexity and matched baseline divergence (KL/JSD). Every
failed attempt is retained and remains in the intention-to-treat denominator.

The remaining phases are GPU-sequential:

1. unload LM Studio, start `llama-server` on `:8090` and the ZLG API on `:9000`;
2. run the capacity-matched official ZLG comparison for every successful source
   attempt, retaining ZLG failures;
3. build paired rows with GPT-2 perplexity, KL/JSD, BLEU, ROUGE, BERTScore,
   self-consistency, capacity, naturalness gates, and post-cluster statistics;
4. run passive-detection and declared robustness/attack benchmarks;
5. run the five blinded LLM judges: standout, weak-link, suspicion,
   attribution, and register;
6. audit the judge pilot before full judging and publish clustered summaries.

ZLG and LM Studio must not cold-load together on this 16 GB GPU. The phases are
separate to prevent memory contention, not to omit either baseline. LLM judges
run only after paired rows exist, so no judge result should be reported before
the ZLG phase completes.

## Current verification

- Focused campaign/runner tests: 7 passed.
- Ruff on the new campaign code: passed.
- Frozen corpus: 674 eligible / 772 angle artifacts.
- Pilot: started as a background process on 2026-08-15 (150 planned attempts).
- The first pilot process was stopped at user request after 44 successes and 10
  failures when Windows warned about low C: space from LM Studio logging. Its
  artifacts were preserved as `runs/batch_0001_interrupted_20260815T1559`.
  D: retained about 124 GB free. After LM Studio logging was addressed, a clean
  `batch_0001` pilot was restarted; no interrupted result was deleted or mixed
  into the confirmatory batch.
- Full ZLG, reference metrics, attacks, passive detector, and LLM judges:
  pending the pilot gate.

### Restart and full-run continuation

After a Windows restart, the clean pilot was confirmed complete at 150/150
accounted attempts: 110 successful stegotexts and 40 failures (73.3%
intention-to-treat success). Conditional receiver recovery was 100%; failures
were 33 receiver-angle mismatches, six generation failures, and one invalid
JSON response. Because 73.3% is below the documented 80% pilot threshold, the
full continuation is exploratory and must not be described as a passed
confirmatory gate. At the user's explicit request, the resumable full campaign
was started on 2026-08-15; it skipped completed batch 1 and began batch 2 while
retaining every pilot failure.

A later Windows restart interrupted `batch_0003` mid-run (61 successes, 28
failures). Those artifacts were preserved as
`runs/batch_0003_interrupted_20260815T1825`. Batches 1–2 remained complete
(150/150 each). The full campaign was resumed again on 2026-08-15; it skipped
completed batches 1–2 and started a clean `batch_0003`.

The `/lucid` page in `stego-results-viewer` reads this campaign live and shows
the manifest totals plus current success/failure artifact counts.

### Babysit ownership

As of 2026-08-15 the Cursor agent owns end-to-end babysitting of this campaign
(generation → ZLG → paired metrics/attacks → LLM judges). State lives in
`babysit_state.json`. Health ticks use
`scripts/babysit_lucid_campaign_tick.ps1`. Incomplete batches are archived, never
deleted; LM Studio and ZLG stay GPU-sequential.
