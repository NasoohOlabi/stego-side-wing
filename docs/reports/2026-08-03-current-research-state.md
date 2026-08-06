# Current research state — 2026-08-03

This is the authoritative current-state note for active LUCID and ZLG work.
Historical reports remain historical and must not be read as the latest result.

## Active LUCID sample-generation run

- Artifact: `metrics/e2e_runs/LUCID_context_weighted_v2_balanced_500`.
- Run ID: `20260802T065317Z`; one `balanced` lane.
- Intended bulk size: **500 attempted samples**, not 500 independent posts.
- Current terminal artifact state: **381 succeeded, 119 failed (76.2% success)**.
  Receiver recovery is 100% among successful outputs.
- The run selected 64 unique frozen source posts and reused them. Any inference
  must cluster by source post; it is not a 500-independent-post study.
- Failures: 108 `receiver_angle_mismatch` and 11 `generation_failure`. This is
  a blocked generation artifact, not a completed benchmark claim.

## Method and provenance

The LUCID lane uses model-generated, cached angle artifacts with
`schema_version: 3`,
`artifact_namespace: selection_channel_angles/context_weighted_v2`,
`generator_version: efficient_multiframe_selection_v1`,
`sampler_version: context_weighted_v2`, and 32 retained angles per post.

The runner enforced receiver decoding and model generation. Its progress JSON
failed to capture `git_commit` or `git_branch` and records
`git_status_clean: false`; the artifact is consequently **not reproducibly
pinned to an exact source revision**. It started after committed base `0353848`
on branch `fix/zlg-benchmark-overhaul`, but it also used a dirty worktree. Cite
it as "dirty worktree based on 0353848", not as an exact commit-level result.

## ZLG comparison lane

The latest completed recalibrated ZLG artifact is
`metrics/zlg_comparison_runs/zlg_batch_scale300_recalibrated`: **510 accepted
of 554 capacity-matched rows (92.1%)**. It is paired with the older
`scale300_combined_summary` source, not with the current LUCID 500-attempt
artifact. Its server identity is ZGLS commit `11cab87`, model
`Qwen3.5-9B-Q4_K_M.gguf`, with a dirty server worktree. Do not compare the two
lanes as a paired final result until a frozen, symmetric dataset is built.

## Next action

Make every future workload run write the Git commit, branch, dirty state, exact
command, model identity, and frozen source-manifest hash before execution. The
current LUCID failures need diagnosis/retry before a ZLG-paired benchmark is
authorized.
