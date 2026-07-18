# Publication benchmark implementation report

**Date:** 2026-07-18  
**Timezone:** Asia/Damascus  
**Repository:** `stego-side-wing`  
**Status:** Implementation and automated validation complete; live benchmark generation not yet run

## Purpose

This work turned the existing sample-generation and ZLG comparison code into a
publication-oriented benchmark framework. The main requirements were:

1. make frame capacity depend on each post's actual comments and TangentsDB;
2. prepare 100 additional real-source posts instead of adding more demo rows;
3. compare against the official Zero-shot Generative Linguistic Steganography
   (ZGLS/ZLG) method with equivalent payload and resource constraints;
4. add stronger naturalness, detectability, robustness, capacity, and
   statistical metrics; and
5. make the experiment reproducible, resumable, and honest about failures.

No workflow or system LLM prompt text was changed during this work. The LLM
judge and paraphrase tools deliberately require separately frozen prompt files.

## Current truth about samples

The framework can prepare and run 100 real-source benchmark posts, but it has
not yet produced 100 new live stego samples.

- The preparation path selects real stored post/comment artifacts.
- The benchmark runner invokes the real sender and receiver pipelines.
- The official ZLG lane invokes a real ZLG HTTP service or supported local
  model path.
- Unit tests use mocks and synthetic fixtures; they are smoke/regression data,
  not research samples.
- Existing historical JSON artifacts were not relabeled as newly generated
  publication results.

Consequently, there are currently no new empirical claims from this work. It
implemented the protocol and tooling needed to generate those claims safely.

## 1. Dynamic frame capacity

### Previous risk

The earlier multi-frame path could be interpreted as using a fixed frame size.
That would be incorrect because the recoverable selection channel changes with
the number of available comments and TangentsDB choices in each post.

### Implemented behavior

`selection_channel_capacity_report()` was added to
`src/workflows/utils/stego_codec.py`. For each source post it reports:

- comment choice count;
- TangentsDB choice count;
- physical widths needed to enumerate those choices;
- losslessly recoverable widths; and
- total recoverable capacity in bits.

The recoverable per-frame capacity is:

```text
floor(log2(comment_choices)) + floor(log2(tangent_choices))
```

The use of `floor(log2(...))` is intentional. When a choice count is not a
power of two, using the full physical enumeration width would create indices
that cannot map to a valid choice and would break lossless recovery.

`StegoPipeline._multi_frame_slots()` now obtains the capacity report from the
actual post. Frame planning therefore changes with the post's comment tree and
TangentsDB rather than using a global constant.

Each planned frame now retains:

- `capacity`;
- `capacity_report`;
- `bits_used`; and
- `padding_bits`.

The payload stream is split across as many dynamic frames as required, subject
to configured carrier and word ceilings. Padding is accounted for explicitly;
it is not reported as useful payload.

### Coverage added

Tests cover:

- zero-choice behavior;
- power-of-two counts;
- non-power-of-two counts;
- capacity changes when comment/TangentsDB counts change;
- multi-frame artifacts and accounting; and
- sender/receiver recovery symmetry.

## 2. Official ZLG baseline integration

### Capacity-matched lane

The primary comparison carries the same 64 useful payload bits for both
methods. The ZLG service no longer calls `/capacity_probe` during ordinary
target-payload comparisons.

`run_comparison_frames()` in `src/services/zlg_comparison_service.py` now:

- creates independently decodable ZLG carriers;
- verifies every accepted segment with the ZLG receiver;
- retries an exact shorter prefix when a carrier only partially embeds the
  remaining payload;
- aggregates only verified useful bits;
- limits the run to the configured carrier and total-word ceilings; and
- records each carrier, its useful bits, framing overhead, word count, and
  receiver result.

A partial carrier is never counted as recovered payload unless the exact
shorter payload is generated again and independently decoded.

### Maximum-capacity lane

The `max_capacity` comparison is separate from the capacity-matched claim.

For the selection method:

- the theoretical search ceiling comes from the post-specific comment and
  TangentsDB capacity;
- candidate payloads are planned from largest to smallest;
- the largest payload must pass real generation, the total-word budget, and
  receiver recovery; and
- if it fails, the runner tries the next smaller planned payload and records
  every capacity trial.

For ZLG:

- verified capacity candidates extend through 256 useful bits per carrier;
- ZLG receives the same maximum carrier count, total-word ceiling, and retry
  budget as the selection method;
- a carrier candidate must pass decode, quality, leakage, and remaining-word
  checks;
- the highest verified candidate fitting the remaining budget is selected;
- capacity is summed across the allowed carriers; and
- `capacity_censored` records whether the probe reached its configured upper
  candidate, meaning the observed value may only be a lower bound.

This corrected an interim implementation in which `max_capacity` was accepted
as a command-line option but did not alter dynamic ZLG frame generation.

### Fair accounting

Both methods now record separately:

- useful payload bits targeted;
- useful payload bits verified;
- transformed payload bits where applicable;
- protocol/framing overhead bits; and
- total embedded bits.

Failed attempts remain in the attempt-level data. Conditional capacity metrics
exclude rejected attempts, while intention-to-treat success/failure reporting
retains them.

## 3. Real-post preparation and frozen provenance

### Post preparation

`scripts/prepare_publication_posts.py` prepares 100 real-source post artifacts.
It excludes post IDs already used by the legacy ZLG demonstration so the new
evaluation is not an accidental replay of the old showcase set.

The output is a fixed post-ID list plus prepared angle artifacts suitable for
the paired runner. A failed post is not replaced after the manifest is frozen.

### Benchmark manifest

`scripts/benchmark_preflight.py` creates a frozen manifest containing:

- Git commit and branch;
- dirty-tree status;
- ordered post IDs and their aggregate hash;
- model-manifest path and SHA-256;
- protocol path and SHA-256;
- every selected angle-artifact SHA-256;
- every selected dataset-artifact SHA-256 when supplied; and
- deterministic per-post payload seed, bit length, and payload SHA-256.

The primary payload assignment is deterministic and exactly 64 UTF-8 bits.
The manifest permits paired reruns without logging the plaintext payload.

### Runtime provenance

`scripts/run_publication_benchmark.py` refuses dirty manifests or a dirty
working tree by default. `--allow-dirty` exists only for exploratory runs.

Every run signature includes:

- the complete frozen manifest;
- comparison mode;
- carrier, word, retry, and token ceilings;
- ZLG server URL;
- a runtime fingerprint over relevant Python, configuration, and workflow
  files; and
- the live ZLG server identity.

For an HTTP ZLG server, `/health` must report an operational status and loaded
model. A server implementation version must either be returned by `/health` or
supplied with `--zlg-server-version` as a deployed commit/image digest. The
identity is written to `zlg_server_identity.json`, hashed into each row, and
included in the run signature.

Old result rows and pilot summaries are ignored unless their signature exactly
matches the current manifest, code/configuration, server identity, and run
parameters.

## 4. Paired benchmark runner

`scripts/run_publication_benchmark.py` implements the main execution protocol.

### Pairing

Each post ID is evaluated by:

- `our_method`; and
- `official_zgls`.

The capacity-matched lane uses the same deterministic 64-bit useful payload,
source post, and seed for both methods.

### Staging and expansion gate

The supported stages are:

- `pilot`: run the first 25 frozen posts only;
- `full`: continue only when a matching passing pilot summary exists; and
- `auto`: run the 25-post pilot and expand to 100 only if the gate passes.

The expansion gate requires:

- every post/method tuple to have an attempt row;
- accounting invariants to pass;
- at least 80% generation acceptance for each method; and
- at least 95% verified recovery among accepted carriers for each method.

When recomputing a pilot after a completed run, only the first 25 frozen post
IDs are used. Rows from the remaining 75 posts cannot inflate or invalidate the
pilot calculation.

### Resume and failures

Resume keys are `(post_id, method)` within the exact run signature. The runner
retains:

- accepted attempts;
- generation failures;
- quality failures;
- decode failures;
- partial ZLG results;
- word-budget failures; and
- receiver artifacts for later attack evaluation.

Failures are part of the evaluated method; they are not silently replaced with
another post.

## 5. Metrics and robustness tooling

### Main results analysis

`scripts/analyze_publication_results.py` reports:

- attempted, accepted, and failed counts;
- generation success rate;
- exact receiver recovery;
- failure taxonomy;
- recovered useful bits per word;
- latency;
- perplexity;
- matched-post divergence metrics, including JSD;
- intention-to-treat results and successful-output-only results separately;
- paired post-cluster bootstrap confidence intervals; and
- Holm correction within declared secondary hypothesis families.

Post ID is the independent cluster. Repeated carriers from one post are not
treated as independent observations.

### Passive detector

`scripts/analyze_passive_detector.py` implements a reproducible character
4-gram Naive Bayes detector and grouped evaluation by post ID. It reports ROC
AUC without leaking carriers from the same source post across train/test
groups.

### Suspiciousness evaluation

The suspiciousness pipeline is split into separate stages:

- `build_suspiciousness_tasks.py` creates blinded and position-rotated tasks;
- `run_suspiciousness_judge.py` calls the judge using an externally frozen
  prompt file;
- `score_suspiciousness_judgments.py` joins the hidden answer key; and
- `analyze_suspiciousness_results.py` aggregates the results.

The retained provenance includes judge model, prompt hash, decoding settings,
candidate ordering, raw response, and parser version. The intended judge is a
different model family from the evaluated generator.

### Robustness attacks

The attack framework includes:

- deterministic word deletion;
- synonym substitution;
- sentence deletion;
- sentence reorder;
- context mutation; and
- low/medium/high LLM paraphrase attacks.

Relevant files are:

- `src/services/benchmark_attack_service.py`;
- `scripts/run_paraphrase_attacks.py`;
- `scripts/build_attack_corpus.py`;
- `scripts/run_attack_receivers.py`; and
- `scripts/analyze_attack_recovery.py`.

Attacked text is evaluated by the corresponding real receiver, not by a shared
heuristic. ZLG context mutation removes one original prompt example, matching
the selection method's removal of one original context comment rather than
constructing a different prompt from scratch.

Attack recovery is intended for the capacity-matched lane. Some ZLG capacity
probe implementations do not return their generated secret material, so a
maximum-capacity carrier may be independently verified at generation time but
not replayable for later attack decoding.

## Configuration and dependency changes

New benchmark configuration files:

- `config/benchmark_models.json`;
- `config/benchmark_protocol.json`.

The model policy records Qwen as the evaluated generator family, Gemma as the
cross-family judge/paraphraser, and GPT-OSS as a future validation family.

NLTK was added to the metrics dependency set for WordNet-backed synonym
attacks. `uv.lock` was regenerated successfully with `uv lock --offline`.

## Automated validation performed

The following validation was completed after the final changes:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pyright
.\.venv\Scripts\python.exe -m ruff check <affected benchmark files>
git diff --check
```

Results:

- full pytest suite passed with one existing skip;
- Pyright passed with 0 errors and 0 warnings;
- Ruff passed for all affected benchmark, codec, service, runner, and test
  files;
- `git diff --check` found no whitespace errors; and
- only existing line-ending conversion warnings and a `requests` dependency
  compatibility warning remained.

`uv run pytest` and `uv run pyright` intermittently failed on this Windows
machine with `Failed to canonicalize script path`. Running the same installed
tools through `.venv\Scripts\python.exe -m ...` completed successfully.

## Remote-box assistance and independent review

The configured SSH host `asus` was used as requested.

Observed remote environment:

- host: `DESKTOP-G71HMQ7`;
- GPU: NVIDIA RTX 5060 Ti;
- Codex CLI: `0.144.1`;
- isolated review checkout: `C:\Users\ASUS\codex-stego-review`; and
- available generator model:
  `C:\Users\ASUS\.lmstudio\models\lmstudio-community\Qwen3.5-9B-GGUF\Qwen3.5-9B-Q4_K_M.gguf`.

Remote Codex was run read-only against the isolated checkout. Its useful
findings led to these fixes:

1. completed 100-post result files no longer corrupt a recomputed 25-post
   pilot gate;
2. ZLG context mutation now removes exactly one existing cover example;
3. conditional effective-BPW inference excludes rejected attempts;
4. over-budget ZLG frame word counts are retained correctly;
5. resume and pilot summaries are bound to a complete run signature;
6. `max_capacity` now performs an actual capacity search;
7. our method falls back from an unverified maximum to the largest verified
   smaller payload;
8. both methods receive the same carrier and word ceilings; and
9. the external ZLG model/backend/server version is included in provenance.

Some remote findings were checked and rejected when the local control flow
already guaranteed the invariant. For example, an over-word-budget final ZLG
frame leaves its payload unconsumed, so it cannot be returned as an accepted
complete payload. Regression tests were still added around the boundary.

## Blockers to a live run

The benchmark was not started because its required live infrastructure is not
currently complete:

1. no local services were listening on the expected ZLG, LM Studio, workflow
   API, or judge ports during inspection;
2. the local machine did not contain the required Qwen or Gemma model files;
3. the remote machine contains the exact Qwen GGUF but no Gemma model was
   found;
4. `scripts/stego_api_server.py`, referenced by the workspace-level
   `STEGO_API_SERVER.md`, is absent locally, absent from repository history,
   and was not found under the remote user's files; and
5. the repository working tree contains many pre-existing unrelated changes,
   so it cannot yet produce a clean publication manifest.

The missing ZLG server is especially important. An ordinary OpenAI-compatible
chat endpoint is not automatically equivalent to the official ZGLS codec; the
baseline needs the correct token-probability hiding and extraction behavior.
No smoke/mock service should be presented as the official baseline.

## Recommended next execution sequence

1. Recover or implement the documented, versioned ZLG `/health`, `/hide`,
   `/reveal`, and `/capacity_probe` service against the official codec.
2. Deploy it beside the remote Qwen3.5-9B GGUF and record its commit or image
   digest.
3. Install or make available the frozen Gemma judge/paraphraser model.
4. Separate and reconcile unrelated working-tree changes, then commit the exact
   code/configuration being evaluated.
5. Prepare the 100 real posts and freeze the benchmark manifest.
6. Run a five-post infrastructure smoke test.
7. Run the 25-post paired pilot and inspect retained failures.
8. Expand to 100 only if the automated gate passes.
9. Freeze the successful-output corpus and run passive detection,
   suspiciousness judging, and robustness attacks.
10. Generate the final paired statistical report without mixing
    capacity-matched and maximum-capacity claims.

## Primary commands after infrastructure is ready

Prepare posts:

```powershell
uv run python scripts/prepare_publication_posts.py --count 100
```

Freeze the manifest:

```powershell
uv run python scripts/benchmark_preflight.py `
  --post-ids metrics/benchmark/post_ids.txt `
  --angles-dir metrics/benchmark/prepared_angles `
  --dataset-dir datasets/news_cleaned `
  --output metrics/benchmark/manifest.json
```

Run the primary paired lane:

```powershell
uv run python scripts/run_publication_benchmark.py `
  --manifest metrics/benchmark/manifest.json `
  --angles-dir metrics/benchmark/prepared_angles `
  --run-dir metrics/benchmark/runs/primary_64b `
  --zlg-server-url http://<zlg-host>:9000 `
  --zlg-server-version <deployed-commit-or-image-digest> `
  --stage auto
```

Run the separate maximum-capacity lane:

```powershell
uv run python scripts/run_publication_benchmark.py `
  --manifest metrics/benchmark/manifest.json `
  --angles-dir metrics/benchmark/prepared_angles `
  --run-dir metrics/benchmark/runs/max_capacity `
  --zlg-server-url http://<zlg-host>:9000 `
  --zlg-server-version <deployed-commit-or-image-digest> `
  --comparison-mode max_capacity `
  --stage auto
```

## Main files added

- `config/benchmark_models.json`
- `config/benchmark_protocol.json`
- `docs/results/publication-benchmark.md`
- `scripts/benchmark_preflight.py`
- `scripts/prepare_publication_posts.py`
- `scripts/run_publication_benchmark.py`
- `scripts/analyze_publication_results.py`
- `scripts/analyze_passive_detector.py`
- `scripts/build_suspiciousness_tasks.py`
- `scripts/run_suspiciousness_judge.py`
- `scripts/score_suspiciousness_judgments.py`
- `scripts/analyze_suspiciousness_results.py`
- `scripts/run_paraphrase_attacks.py`
- `scripts/build_attack_corpus.py`
- `scripts/run_attack_receivers.py`
- `scripts/analyze_attack_recovery.py`
- `src/services/benchmark_attack_service.py`
- focused tests for every new preparation, runner, analyzer, detector, judge,
  attack, and receiver component

## Main files modified

- `src/workflows/utils/stego_codec.py`
- `src/workflows/pipelines/stego.py`
- `src/workflows/pipelines/receiver.py`
- `src/services/zlg_comparison_service.py`
- `scripts/run_zlg_batch_comparison.py`
- `pyproject.toml`
- `uv.lock`

This list intentionally excludes unrelated pre-existing dirty-tree changes.
Git status must be reviewed and separated before creating the publication
commit and manifest.
