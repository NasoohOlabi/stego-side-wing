# Unified Experiment Plan

This plan covers three coordinated tracks:

- Pareto search
- Model ablation study
- Sample generation for metrics

## Goal

Produce one clean, defensible benchmark package where generated samples, Pareto rankings, and model-ablation conclusions share the same provenance, sample set policy, failure taxonomy, and progress reporting.

## Operating Rule

Do not mix results across different code, config, prompt, dataset, backend, model, or metric logic.

A run is final-report eligible only when:

- `git_status_clean = true`
- code commit is recorded
- variant/model config is recorded
- dataset manifest is recorded
- prompt/config hashes are recorded
- failures are classified
- all compared lanes use the same fixed post ID list

Dirty-tree runs are exploratory only.

## Shared Phases

### Phase 0: Freeze And Preflight

Purpose: make sure later failures are experiment signal, not environment noise.

Required actions:

- Choose benchmark branch/commit.
- Confirm no prompt file changes are pending unless explicitly approved.
- Record git commit, branch, status, changed files, config hashes, prompt hashes, variant manifest hash, dataset manifest hash, backend, and model.
- Verify LLM backend with one minimal call.
- Run a 5-sample `security_legacy` smoke run.

Pass gate:

- No recurring `404`, auth, timeout, or provider routing failures.
- At least 80% infrastructure success in smoke.
- Metrics output is written.
- Receiver decode is attempted.

### Phase 1: Canonical Sample Set

Purpose: make all tracks comparable.

Required actions:

- Select one fixed real-post ID list.
- Store it in the run directory as `post_ids.json`.
- Use identical post IDs for all variants and model lanes.
- Store dataset file hashes for selected posts.
- Do not replace failed posts with new IDs during benchmark counting.

Recommended sizes:

| Stage | Successful Samples Per Lane | Use |
|---|---:|---|
| Smoke | 5 | backend and path validation |
| Pilot | 25 | runtime and failure estimate |
| Minimum reportable | 50 | directional comparison |
| Recommended | 100 | main benchmark |
| Strong claim | 200+ | stronger cross-topic evidence |

### Phase 2: Sample Generation For Metrics

Purpose: collect the canonical real samples that all metrics and comparisons depend on.

Primary command shape:

```bash
uv run python scripts/run_actual_workload_e2e.py \
  --variant security_legacy \
  --variant sec_v2_anchored \
  --variant sec_v2_natural_then_anchor_retry \
  --samples-per-profile 25 \
  --max-retries 1 \
  --log-level INFO
```

Outputs:

- `metrics/e2e_runs/<run>/summary.json`
- per-variant `output-results/`
- per-variant `metrics/`
- per-variant `failures/`
- `metrics/e2e_runs/latest_actual_workload_e2e.json`

Acceptance:

- Each included variant reaches target successful sample count.
- Infrastructure failures are rerun against the same post ID.
- Generation and decode failures are counted against the variant.
- Metric failures are reported separately and fixed/rerun before final tables.

### Phase 3: Pareto Search

Purpose: rank variants across reliability, stealth, overhead, and diversity.

Primary command shape:

```bash
uv run python scripts/run_pareto_search.py \
  --variant security_legacy \
  --variant sec_v2_anchored \
  --variant sec_v2_natural_then_anchor_retry \
  --synthetic-samples 200 \
  --synthetic-payload-sizes 49,96,512 \
  --real-samples 25 \
  --max-retries 1 \
  --log-level INFO
```

Stage order:

- synthetic screen first
- real screen second
- promote only stable real-screen variants to larger sample runs

Outputs:

- `metrics/pareto_runs/pareto_<timestamp>/summary.json`
- `leaderboard.json`
- `frontier.json`
- `latest_heartbeat.json`

Pareto objectives:

| Objective | Direction |
|---|---:|
| receiver_success_rate | higher |
| matched_post_kl | lower |
| matched_post_jsd | lower |
| hidden_expansion_ratio | lower |
| standard_fallback_rate | lower |
| unique_selection_signatures | higher |
| bps_total | higher, if capacity is the goal |

Acceptance:

- Synthetic results are regression/sanity evidence only.
- Real-screen results drive candidate selection.
- No variant is ranked from runs with major infrastructure contamination.

### Phase 4: Model Ablation Study

Purpose: measure model/provider effect on naturalness and metric quality while holding variant and posts fixed.

Default variant:

- `balanced` for naturalness baseline unless explicitly testing security-model interaction.

Primary command shape:

```bash
uv run python scripts/run_model_naturalness_ablation.py \
  --samples-per-model 25 \
  --lm-studio-model openai/gpt-oss-20b \
  --lm-studio-model qwen/qwen3.5-9b \
  --max-gemma-models 4 \
  --max-retries 1 \
  --log-level INFO
```

Outputs:

- `metrics/model_ablation_runs/model_naturalness_<timestamp>/summary.json`
- `leaderboard.json`
- `judge_samples.jsonl`
- `judge_instructions.md`
- per-lane output folders

Acceptance:

- Preflight unavailable models are skipped, not treated as model failures.
- Available lanes use the same selected post IDs.
- Judge samples are blinded by `lane_id`.
- Final ranking uses judge score first, then JSD/KLD/perplexity/failure rate.

### Phase 5: Unified Analysis

Purpose: produce one final interpretation instead of separate disconnected results.

Final report should include:

- run provenance table
- sample generation table
- Pareto leaderboard
- Pareto frontier
- model-ablation leaderboard
- failure breakdown
- final recommendation by goal

Recommendation categories:

| Category | Selection Rule |
|---|---|
| Reliability | highest receiver success, lowest decode failures |
| Stealth | lowest matched-post KLD/JSD with enough samples |
| Security | secure transform plus reliability and acceptable stealth |
| Naturalness | best blind judge score with acceptable metrics |
| Capacity | highest bps with acceptable reliability and stealth |

## Shared Failure Taxonomy

| Class | Meaning | Counts Against Variant/Model? |
|---|---|---|
| infrastructure_failure | network, provider, auth, timeout, HTTP 404/5xx | no, rerun same post |
| generation_failure | model returned unusable output | yes |
| decode_failure | receiver could not recover payload | yes |
| metric_failure | metrics could not be computed | no, fix/rerun metrics |
| data_failure | missing/malformed post, missing angles | no, fix sample list |
| judge_failure | missing/invalid human or LLM judge rating | no, rerun judging |

## Progress Tracking Standard

Every run directory should have a `progress.json` with this shape:

```json
{
  "run_id": "20260504T000000Z",
  "track": "sample_generation|pareto_search|model_ablation",
  "status": "not_started|preflight|running|blocked|complete|invalid|exploratory",
  "stage": "freeze|sample_set|smoke|pilot|main|analysis",
  "git_commit": "",
  "git_branch": "",
  "git_status_clean": false,
  "target_successful_samples_per_lane": 25,
  "lanes": [
    {
      "lane_id": "security_legacy",
      "lane_type": "variant|model",
      "requested": 25,
      "succeeded": 0,
      "failed": 0,
      "infrastructure_failures": 0,
      "generation_failures": 0,
      "decode_failures": 0,
      "metric_failures": 0,
      "data_failures": 0,
      "judge_failures": 0,
      "receiver_success_rate": null,
      "matched_post_kl": null,
      "matched_post_jsd": null,
      "perplexity": null,
      "judge_naturalness_mean": null,
      "last_updated_utc": ""
    }
  ],
  "artifacts": {
    "post_ids": "",
    "summary": "",
    "leaderboard": "",
    "frontier": "",
    "judge_samples": "",
    "latest_heartbeat": ""
  },
  "blockers": [],
  "next_action": ""
}
```

Minimum status update format:

```text
Track: <sample_generation|pareto_search|model_ablation>
Stage: <smoke|pilot|main|analysis>
Status: <running|blocked|complete|invalid|exploratory>
Progress: <succeeded>/<target> successful per lane
Failures: infra=<n>, generation=<n>, decode=<n>, metric=<n>, data=<n>, judge=<n>
Current artifact: <path>
Next action: <one concrete action>
```

## Stop Conditions

Stop and mark the run `blocked` or `invalid` if:

- infrastructure failures exceed 10% of attempts
- recurring LLM `404`, auth, or timeout errors appear
- dataset files change mid-run
- git working tree becomes dirty during a final run
- prompt/config files change mid-run
- compared lanes do not share the same post IDs

## Execution Order

1. Freeze code and record provenance.
2. Build fixed post ID list.
3. Run 5-sample smoke sample generation.
4. Run 25-sample pilot sample generation for shortlisted variants.
5. Run Pareto search to select finalists.
6. Run model ablation on the same post set.
7. Promote finalists to 100 successful samples per lane.
8. Generate final unified report from clean artifacts only.
