# Efficient Multi-Frame Selection Channel Testing Plan

## 1. Objectives

Validate that bounded, short, relevant tangent generation improves efficiency without
breaking sender/receiver recovery, multi-frame behavior, or comparison integrity.

## 2. Unit tests

### 2.1 Configuration

- balanced profile enables limits by default;
- low/mid/high values resolve correctly;
- explicit overrides win over profiles;
- disabled limits return the unbounded sentinel;
- raw target equals retained target times multiplier;
- zero/invalid multiplier falls back safely;
- capacity settings expose all new fields.

### 2.2 Deterministic sampling

- post body is retained when present;
- search and comment caps are enforced;
- global cap is enforced;
- both search and comment sources survive when budget permits;
- input reorder does not change selected identities when source identities are stable;
- repeated calls produce the same dictionary ID;
- sampler version is present;
- empty and malformed entries are ignored safely.

### 2.3 Adaptive stopping

- cached results count toward the raw target;
- generated results count toward the raw target;
- generation stops after the block that crosses the target;
- returned results never exceed the target;
- `None` preserves exhaustive direct-call behavior;
- source-document indices remain correct after skipped inputs.

### 2.4 Failure isolation

- one malformed block between valid blocks produces a degraded partial result;
- a malformed final block does not discard earlier results;
- all malformed blocks raise;
- transport/provider errors raise immediately;
- corrupt cache files are quarantined and regenerated;
- successful cache files survive a later failure.

### 2.5 Pipeline reporting

- retained output never exceeds target;
- raw target is passed to the backend;
- reached/shortfall fields are correct;
- dictionary sampler metadata is persisted;
- extractive mode remains backend-free and bounded.

## 3. Sender/receiver regression tests

Run:

```powershell
uv run pytest -q `
  src/tests/test_stego_codec.py `
  src/tests/test_pipeline_stego.py `
  src/tests/test_receiver_pipeline.py `
  src/tests/test_stego_roundtrip_golden.py
```

Required invariants:

- selected parent index encodes and decodes exactly;
- selected tangent index encodes and decodes exactly;
- recoverable capacity equals the sum of safe widths;
- dictionary sampling does not alter parent choice count;
- no invisible or non-rendering characters appear.

## 4. Multi-frame tests

Use payloads requiring one, two, three, and five frames.

For every case assert:

- deterministic frame count;
- contiguous payload offsets;
- exact recovery of all bits;
- per-frame capacity is not exceeded;
- one failed middle frame is reported as partial recovery;
- later frames do not silently shift into the failed frame's offset;
- duplicate carrier IDs are rejected or reported;
- frame ordering is stable after serialization.

## 5. Efficiency experiment

### 5.1 Dataset

Freeze at least 25 posts stratified by flattened comment count:

- small: fewer than 32 comments;
- medium: 32–255;
- large: 256–2,047;
- very large: 2,048 or more.

Include `1nf30lv` as the pathological regression case.

### 5.2 Treatments

Run each post with:

| Arm | Input cap | Retained target | Oversample |
|---|---:|---:|---:|
| A | 32 | 16 | 4 |
| B | 64 | 32 | 4 |
| C | 96 | 64 | 4 |
| D | 128 | 128 | 4 |
| Legacy audit | unbounded | unbounded | n/a |

The legacy arm should reuse existing artifacts where possible and must not be rerun on very
large posts merely to reproduce wasted compute.

### 5.3 Metrics

Primary:

- verified recovered bits per GPU-minute;
- exact multi-frame recovery rate;
- frame generation success.

Secondary:

- retained relevant tangents;
- raw-to-retained yield;
- cache hit rate;
- LLM calls and wall time;
- comments/research source mixture;
- perplexity and thread-grounded quality;
- synthetic-detection rate;
- passive-detector ROC-AUC;
- output length and bits per word.

### 5.4 Decision rule

Select the smallest configuration whose lower 95% confidence bound is within one
recoverable bit of the best median per-frame capacity and whose exact recovery is not worse
by more than two percentage points.

Prefer the cheaper arm when capacity intervals overlap. Do not select an arm solely because
it produces more raw angles.

## 6. Factorized-codebook experiment

This is a follow-up experiment, not a release gate.

Candidate axes:

- stance: 4 states;
- speech act: 8 states;
- evidence focus: 8 states;
- tone: 4 states.

Nominal tangent capacity is 10 bits from 1,024 combinations. Test:

- per-axis classification accuracy;
- joint exact recovery;
- confusion matrix and minimum Hamming distance;
- naturalness under combined controls;
- robustness to paraphrase and comment deletion.

Advance only if joint exact recovery meets the existing flat-codebook receiver baseline.

## 7. Performance and soak tests

- process the pathological post under the balanced profile;
- require angle stage completion within 30 GPU-minutes;
- require no more than 64 selected input blocks;
- require no more than the configured raw target;
- interrupt and restart midway, then verify cache reuse;
- inject malformed JSON at 25%, 50%, and final-block positions;
- run three concurrent posts and verify cache integrity.

## 8. Full validation gate

Run:

```powershell
uv run pytest -q
uv run pyright
```

Then run one offline fixture-based end-to-end multi-frame test and one live bounded GPU
pilot. Record exact environment, model, configuration, commit, elapsed time, cache status,
and artifact paths.

## 9. Reporting

The experiment report must contain:

- frozen manifest and post-cluster counts;
- exact commands and environment overrides;
- intention-to-treat and successful-output-only summaries;
- failure taxonomy;
- capacity and efficiency distributions;
- matched-payload ZLG comparison only;
- explicit limitations and any reused cache.

No final research claim is made from the 25-post pilot alone.
