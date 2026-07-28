# Efficient Multi-Frame Selection Channel Specification

Status: implementation target
Method: visible parent/comment selection plus short, relevant tangent selection
Primary optimization objective: verified recoverable bits per GPU-minute

## 1. Problem statement

The selection channel currently treats angle generation as an exhaustive corpus-analysis
task. A large Reddit thread can expand into thousands of comment and research text blocks.
The angle runner processes every block before the pipeline retains only the configured
number of angles. This has three undesirable properties:

1. Generation cost grows linearly with input blocks while recoverable capacity grows only
   logarithmically with the number of choices.
2. A malformed response late in the run can prevent the final post artifact from being
   written, even though earlier per-block results were cached.
3. The default `balanced` profile disables all capacity limits, so the bounded profiles do
   not protect normal sample-generation runs.

The method is strongest when it uses ordinary short comments that remain close to thread
content. Parent selection is cheap and exact because the visible reply location identifies
the selected parent. Tangent selection should therefore use a small, high-quality codebook,
and larger payloads should span multiple independent comments/frames.

## 2. Channel model

For one frame:

```text
parent_bits  = floor(log2(parent_choices))
tangent_bits = floor(log2(verified_tangent_choices))
frame_bits   = parent_bits + tangent_bits
```

Only one-to-one, receiver-recoverable choices count. Aliased physical indices, failed
generations, and tangents that cannot be decoded do not contribute useful capacity.

For `n` independent frames:

```text
payload_capacity = sum(frame_bits[i] for i in 0..n-1) - framing_overhead
```

The sender must never increase a reported capacity merely by generating duplicate,
irrelevant, or undecodable tangents.

## 3. Design principles

### 3.1 Parent capacity is the inexpensive channel

The complete visible comment tree remains available to the codec. Angle-input sampling
does not reduce parent choices. A thread such as `1nf30lv`, with 16,283 parent states,
carries 13 parent
bits before any tangent generation occurs.

### 3.2 Tangent capacity uses power-of-two targets

The generator targets the smallest power-of-two codebook that satisfies the configured
frame budget. Producing a choice beyond a power-of-two boundary adds no recoverable bit
until the next boundary is reached.

Recommended normal targets:

| Verified tangents | Tangent bits | Use |
|---:|---:|---|
| 16 | 4 | low-cost smoke/pilot |
| 32 | 5 | default balanced frame |
| 64 | 6 | high-quality capacity frame |
| 128 | 7 | explicit high-capacity experiment |

Targets above 128 require an explicit experiment configuration. They are not normal
sample-generation defaults.

### 3.3 Short, relevant source material

Angle inputs are a deterministic mixture of:

- the post body;
- a bounded set of research snippets;
- a bounded, diverse set of short comments.

Inputs should be close to the thread topic, small enough for reliable structured output,
and stable across reruns. Long pages must be summarized or split before angle generation;
they must not implicitly create an unbounded number of LLM requests.

### 3.4 Multi-frame before exhaustive single-frame expansion

After a frame reaches its tangent target, additional payload uses another carrier. The
system must prefer two independently recoverable 18–20 bit frames over attempting to
construct a single enormous tangent codebook.

### 3.5 Verified capacity, not raw capacity

The reported tangent count is calculated after:

1. schema validation;
2. exact and semantic deduplication where configured;
3. thread-relevance filtering;
4. sender/receiver decoding validation.

Raw model outputs are diagnostic data only.

## 4. Deterministic input selection

### 4.1 Source budgets

The default balanced profile uses bounded source budgets:

```text
research terms:             8
selected research URLs:    24
dictionary search blocks:  16
dictionary comments:       48
total angle input blocks:  64
retained tangent target:   32
```

The high profile uses:

```text
research terms:            12
selected research URLs:    48
dictionary search blocks:  32
dictionary comments:       96
total angle input blocks: 128
retained tangent target:   64
```

### 4.2 Stable diverse sampling

Selection must be independent of Python hash randomization and filesystem enumeration.
Within each source, entries are ranked by a stable SHA-256 key derived from source,
source identifier, and normalized text. The post body is always retained when present.

The global budget is allocated with source-aware round-robin selection so research blocks
cannot consume the entire budget before any comments are considered. The exact selected
entry list and its hash are recorded in the angle report.

### 4.3 Receiver contract

Sampling must be reproducible from the same post artifact and configuration. Reports must
record:

- sampler version;
- effective source budgets;
- selected entry hashes and source identifiers;
- dictionary ID;
- capacity profile.

Any change to sampling order or hashing increments the sampler version.

## 5. Adaptive angle generation

### 5.1 Raw generation budget

Relevance and deduplication can remove candidates, so the backend may generate more raw
angles than the retained target. The default raw target is:

```text
raw_target = retained_target * oversample_factor
oversample_factor = 4
```

Generation stops after completing the current input block once `raw_target` is reached.
The post-level relevance and deduplication stages then retain at most `retained_target`.

### 5.2 Incremental persistence

Each successfully processed text block is cached immediately using its stable content hash.
A post retry must reuse those cached results.

Malformed output from one block follows this policy:

1. use the existing parse/repair attempts;
2. log the failed input hash and failure type;
3. continue with the next block if at least one other block can succeed;
4. fail the post only when no valid angle was produced or a non-recoverable provider error
   occurs.

Authentication, quota, transport, and provider-availability errors remain fatal. They must
not be mislabeled as malformed content.

### 5.3 No prompt changes

This refactor does not change workflow/system prompts. Structured prompt changes remain
subject to the repository's explicit double-confirmation rule.

## 6. Multi-frame protocol requirements

Each frame records:

- frame index and total frame count;
- payload bit offset and useful bit count;
- parent choice count and selected parent index;
- tangent choice count and selected tangent index;
- codec/configuration version;
- exact-recovery result.

Frame allocation is deterministic. The sender fills each frame only to its recoverable
capacity. Failed frames stay in attempt data and do not silently shift later offsets.

The receiver reconstructs each frame independently, orders recovered fragments by frame
index, validates framing/checksum metadata, and reports partial recovery without inventing
missing bits.

## 7. Configuration

Required settings:

| Variable | Meaning |
|---|---|
| `WORKFLOW_CAPACITY_LIMITS_ENABLED` | Enables bounded source and output budgets |
| `WORKFLOW_CODEC_DICTIONARY_LIMITS_ENABLED` | Opt-in coupling of generation budgets to payload-compression dictionaries |
| `WORKFLOW_CAPACITY_PROFILE` | `low`, `mid`, or `high` |
| `WORKFLOW_ANGLES_MAX_OUTPUT` | Retained tangent target |
| `WORKFLOW_ANGLES_RAW_TARGET_MULTIPLIER` | Raw oversampling factor |
| `WORKFLOW_ANGLES_MAX_INPUT_BLOCKS` | Absolute input-block ceiling |
| `WORKFLOW_DICTIONARY_MAX_COMMENTS` | Comment angle-input ceiling |
| `WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS` | Research angle-input ceiling |

`balanced` enables the bounded mid profile by default. Explicit environment overrides keep
priority over profile defaults. Setting `WORKFLOW_CAPACITY_LIMITS_ENABLED=0` remains an
escape hatch for controlled legacy experiments and must emit an unbounded-capacity warning.
Payload-compression dictionaries remain unchanged by default
(`WORKFLOW_CODEC_DICTIONARY_LIMITS_ENABLED=0`) so angle efficiency does not silently change
the sender/receiver codec contract.

## 8. Observability

Each angle run reports:

- raw and selected input counts by source;
- sampler version and dictionary ID;
- retained target, raw target, and oversample factor;
- cache hits, cache misses, processed blocks, and failed blocks;
- early-stop status and reason;
- raw, deduplicated, relevant, and retained angle counts;
- elapsed milliseconds and estimated GPU requests avoided;
- recoverable parent, tangent, frame, and multi-frame bits.

Primary efficiency metric:

```text
verified_recovered_bits / GPU_generation_minutes
```

Secondary metrics include exact recovery, frame success, relevant-angle yield, cache reuse,
naturalness, synthetic-detection rate, and passive-detector ROC-AUC.

## 9. Safety and failure semantics

- Never use invisible or non-rendering carriers.
- Never report raw generated candidates as useful bits.
- Never discard successful cached blocks because a later block is malformed.
- Never continue indefinitely after the target has been reached.
- Never let a failed frame disappear from intention-to-treat results.
- Never compare fluency against ZLG at unequal useful payload without labeling the
  comparison mode.

## 10. Acceptance criteria

The refactor is accepted when:

1. the default balanced profile processes at most 64 input blocks and retains at most 32
   tangents;
2. the angle backend stops once its raw target is reached;
3. one malformed block does not discard earlier valid blocks;
4. repeated runs select the same dictionary and reuse cached results;
5. sender/receiver round-trip tests remain exact;
6. multi-frame tests recover payloads spanning at least three frames;
7. reports expose verified bits per GPU-minute inputs;
8. the full test suite and strict type check pass.

## 11. Research basis

This design retains this project's visible selection channel while adopting the adaptive
budgeting lesson common to efficient probabilistic steganography. Relevant primary work:

- Lin et al., *Zero-shot Generative Linguistic Steganography*, NAACL 2024:
  https://aclanthology.org/2024.naacl-long.289/
- Shen et al., *Near-imperceptible Neural Linguistic Steganography via Self-Adjusting
  Arithmetic Coding*, EMNLP 2020:
  https://aclanthology.org/2020.emnlp-main.22/
- Liu et al., *Linguistic Steganography via Self-Adjusting Asymmetric Number System*,
  Computational Linguistics 2026:
  https://aclanthology.org/2026.cl-1.4/

The proposed factorized tangent codebook is a separate future experiment, not part of the
initial compatibility-preserving refactor.
