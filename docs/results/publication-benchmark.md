# Publication benchmark runbook

The benchmark is paired and provenance-first. Do not mix samples generated
under different commits, prompts, model IDs, or dataset manifests.

The primary lane carries 64 useful payload bits. Each frame dynamically carries
`floor(log2(comment choices)) + floor(log2(flattened TangentsDB entries))`
lossless bits. Eight carriers and 320 words are failure ceilings, not fixed
frame sizes; only the final frame may contain zero padding.

## Freeze inputs

1. Prepare 100 posts not used by the legacy ZLG demo:

```powershell
uv run python scripts/prepare_publication_posts.py --count 100
```

2. Commit the code under evaluation and confirm `git status --short` is empty.
3. Resolve the exact model IDs in `config/benchmark_models.json`.
4. Create the manifest:

```powershell
uv run python scripts/benchmark_preflight.py `
  --post-ids metrics/benchmark/post_ids.txt `
  --angles-dir metrics/benchmark/prepared_angles `
  --dataset-dir datasets/news_cleaned `
  --output metrics/benchmark/manifest.json
```

The manifest is an input artifact and must be retained with all result
directories. Never replace a failed post with a different post.

## Paired execution

Run the 25-post gate and automatically continue to 100 only if it passes:

```powershell
uv run python scripts/run_publication_benchmark.py `
  --manifest metrics/benchmark/manifest.json `
  --angles-dir metrics/benchmark/prepared_angles `
  --run-dir metrics/benchmark/runs/primary_64b `
  --zlg-server-version <deployed-commit-or-image-digest> `
  --stage auto
```

The runner resumes by `(post_id, method)` and retains every failed attempt.
It refuses dirty manifests or worktrees by default. Exploratory dirty-tree runs
must pass `--allow-dirty`; their runtime source/config fingerprint prevents
resume data from being reused after code changes.

## Analysis commands

```powershell
uv run python scripts/analyze_publication_results.py `
  --input metrics/benchmark/runs/primary_64b/attempts.jsonl `
  --dataset-dir datasets/news_cleaned `
  --output metrics/benchmark/runs/primary_64b/results.json

uv run python scripts/analyze_passive_detector.py `
  --input metrics/benchmark/runs/primary_64b/attempts.jsonl `
  --output metrics/benchmark/runs/primary_64b/passive_detector.json
```

Use `build_suspiciousness_tasks.py`, `run_suspiciousness_judge.py`, and the
scorer/analyzer scripts to keep the answer key away from Gemma and retain raw
responses. Generate separately provenanced Gemma rewrites with
`run_paraphrase_attacks.py`, then run `build_attack_corpus.py`,
`run_attack_receivers.py`, and `analyze_attack_recovery.py`. Both LLM runners
require externally frozen prompt files, keeping benchmark prompts isolated from
workflow prompts.

## Run order

1. Five-sample smoke run for one stable variant.
2. Twenty-five-sample pilot for every candidate lane.
3. Freeze analysis code and select primary lanes.
4. Run the confirmatory paired workload using the same post IDs, payloads, and
   seeds for every lane.
5. Run attacks and passive detection against the frozen outputs.
6. Run the untouched validation split only after selecting the final lane.

Infrastructure failures may be retried on the same tuple. Generation,
validation, and decode failures count against the evaluated lane.

## Statistical unit and missingness

- The independent unit is a unique post ID. Repeated generations, payloads,
  slots, and judge calls from the same post are clustered before inference.
- Use a paired post-cluster bootstrap with at least 10,000 resamples for mean
  differences. Row-level tests may be shown only as descriptive diagnostics.
- Report all attempted tuples. Quality metrics conditional on successful
  generation must be labeled as such and accompanied by attempt-level failure
  rates; rejected outputs must not silently disappear from the comparison.
- Apply Holm correction within each declared family of secondary hypotheses.

## Capacity comparisons

Run and label two different experiments:

1. `capacity_matched`: both methods target the same useful payload bits.
2. `max_capacity`: each method searches its own maximum under the same cover
   length, quality gate, retry budget, model, and decode-verification rules.

Run the secondary capacity lane in a separate directory:

```powershell
uv run python scripts/run_publication_benchmark.py `
  --manifest metrics/benchmark/manifest.json `
  --angles-dir metrics/benchmark/prepared_angles `
  --run-dir metrics/benchmark/runs/max_capacity `
  --zlg-server-version <deployed-commit-or-image-digest> `
  --comparison-mode max_capacity `
  --stage auto
```

The selection method derives its useful-payload ceiling from each post's
comment and TangentsDB choice counts. ZGLS probes verified payload sizes up to
256 bits per carrier; both methods receive the same carrier, 320-word, and
retry ceilings. The runner records when ZGLS reaches the probe ceiling, and it
hashes the live `/health` model/backend configuration into the run signature.
Attack recovery belongs to the capacity-matched lane because capacity-probe
payload material may not be returned by every ZLG server implementation.

Never use a maximum-capacity probe as evidence for a capacity-matched claim.

## Judge reproducibility

Retain the exact judge prompt hash, model ID, decoding parameters, decoy source
IDs, candidate ordering seed, raw response, and parser version. Rotate candidate
positions and use a judge model family that is not the evaluated generator.

## Required report fields

Report requested, succeeded, and failed counts; failure taxonomy; exact model
and variant; receiver recovery; recovered bits per word; matched-post KL/JSD;
perplexity; latency; passive detector AUC; judge suspiciousness; and recovery
under each attack severity. Include paired bootstrap confidence intervals and
the complete manifest path for every table.
