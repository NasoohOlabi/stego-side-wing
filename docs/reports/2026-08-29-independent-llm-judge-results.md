# Independent LLM-judge result â€” 2026-08-29

## Scope

This note records the completed LLM-judge evaluation for the fresh LUCID vs
ZLG comparison. It is a fixed-text, post-clustered judge evaluation. It is not
a new capacity experiment, a human-subject study, or a replacement for the
intention-to-treat reliability accounting in the source run.

## Frozen evaluation set

- Source comparison dataset:
  `metrics/zlg_comparison_runs/zlg_lucid_fresh_6x_20260815/comparison_dataset/paired_rows.jsonl`
- Judge dataset:
  `metrics/zlg_comparison_runs/zlg_lucid_fresh_6x_20260815_independent_unique_judge`
- Selection rule: first complete pair for each post whose ZLG `stegotext` is
  globally unique in the 1,608-pair source dataset.
- Source pool: 1,608 pairs from 339 posts.
- Selected set: 244 pairs from 244 posts (488 generated texts).
- Text uniqueness in the selected set: 244 / 244 for our method and 244 / 244
  for ZLG. The 95 posts without a unique ZLG output were excluded.

The five judge criteria reuse these fixed pairs to measure different
properties. They are not 2,928 independent generated samples.

## Execution and provenance

- 2,928 / 2,928 judge tasks completed; 0 task errors.
- Pilot audit passed with full valid coverage for all five criteria.
- The completed run explicitly used Codex Luna with high reasoning effort;
  per-task prompt/schema hashes, model provenance, and JSONL responses are in
  `comparison_dataset/codex_judgments/`.
- After this run, the default for future judge runs is Claude Sonnet 5 via the
  Claude CLI `sonnet` alias. This default change does not rewrite or mix with
  the completed Codex result.

## Results

All figures below use the 244 selected post clusters unless noted otherwise.
Only the register result has a reported paired post-cluster significance test
in the scoring artifact. The remaining raw differences are descriptive.

| Criterion | Our method | ZLG | Interpretation |
| --- | ---: | ---: | --- |
| Standout detection | 126 / 244 (51.6%) | 69 / 244 (28.3%) | Lower is better; ZLG was less often identified in the 10-way slate. |
| Weakest text picked | 71 / 244 (29.1%) | 116 / 244 (47.5%) | Lower is better; the human control was picked 57 / 244 (23.4%). |
| Suspicion AUROC vs human | 0.661 | 0.650 | Lower is closer to chance/human; no clear post-cluster difference (p=0.385). |
| Thread attribution accuracy | 89.3% | 75.0% | Higher indicates stronger thread-specific linkage; descriptive only here. |
| Register-fit score (1â€“5) | 3.193 | 2.791 | Our method higher by 0.402 post-cluster points; 106 wins, 67 losses, 71 ties; paired sign-test p=0.00374. |

## Interpretation limits

- This selection deliberately removes repeated ZLG covers and uses one pair per
  post. It addresses non-independence in the earlier raw judge workload, but it
  also changes the evaluand: the result is about the unique-output subset.
- Judge scores are model-based measures, not reader studies. They must not be
  reported as proof of human authorship, security, or general naturalness.
- Capacity remains non-comparable across these systems without a separately
  frozen, capacity-matched experiment using real receiver verification.
- Report failure and acceptance rates from the source comparison run alongside
  these conditional fixed-text judgments; do not combine their denominators.

## Presentation

The existing `/zlg-comparison` viewer page now loads this independent judge
run by default, shows its selection/provenance and completion state first,
corrects metric-specific judge denominators, and exposes search over the paired
examples. The matching Markdown export is `/zlg-comparison.md`.
