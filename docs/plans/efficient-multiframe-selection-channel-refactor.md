# Efficient Multi-Frame Selection Channel Refactor

This document defines the implementation sequence for
`efficient-multiframe-selection-channel-spec.md`.

## 1. Refactor boundaries

In scope:

- bounded default capacity profiles;
- deterministic source-aware dictionary sampling;
- adaptive raw-angle stopping;
- malformed-block failure isolation;
- capacity and efficiency observability;
- preservation of the existing sender/receiver codec contract;
- tests and operational documentation.

Out of scope:

- workflow/system prompt changes;
- token-probability coding;
- replacement of the official ZLG baseline;
- a new factorized tangent codec;
- changes to invisible-character policy;
- changing the mathematical definition of recoverable selection capacity.

## 2. Configuration refactor

File: `src/infrastructure/config.py`

1. Enable capacity limits in the `balanced` encoding profile.
2. Replace research-heavy profile tiers with short-comment-oriented budgets.
3. Add `WORKFLOW_ANGLES_RAW_TARGET_MULTIPLIER`, defaulting to `4`.
4. Add a getter for the derived raw target:

   ```text
   max_output * raw_target_multiplier
   ```

5. Include both values in `get_workflow_capacity_settings()`.
6. Preserve explicit environment overrides and the legacy unbounded escape hatch.

Compatibility:

- `WORKFLOW_CAPACITY_LIMITS_ENABLED=0` restores unbounded behavior.
- Existing per-key integer overrides retain precedence.
- No persisted artifact schema field is removed.

## 3. Dictionary sampling refactor

File: `src/workflows/utils/text_utils.py`

Introduce pure helpers:

- normalized stable entry key;
- deterministic per-source ranking;
- source-budget selection;
- round-robin global-budget selection.

Rules:

1. retain the post body first;
2. deterministically rank research and comment entries;
3. apply per-source budgets;
4. fill the global budget round-robin across search results and comments;
5. preserve source metadata and emit sampler version `stable_round_robin_v1`.

The dictionary report adds:

- `sampler_version`;
- `selection_strategy`;
- selected source counts;
- existing dictionary ID and text hashes.

## 4. Backend contract refactor

Files:

- `src/workflows/ports.py`
- `src/workflows/adapters/backend_api.py`
- `src/services/workflow_backend_client.py`
- `src/services/angles_service.py`

Add the optional keyword:

```python
max_results: int | None = None
```

It is an upper bound on raw validated angle records returned by the backend. `None`
preserves the legacy exhaustive behavior for direct callers.

The workflow pipeline always passes its derived raw target.

## 5. Angle runner refactor

File: `src/content_acquisition/angles/angle_runner.py`

Both LM Studio and workflow-LLM paths:

1. check cache for each input block;
2. append validated cached or generated results;
3. stop after the current block when `max_results` is reached;
4. truncate the returned raw list to `max_results`;
5. record processed, failed, cached, and generated counts.

Recoverable malformed-response errors are isolated per input block. Provider/system errors
still propagate.

If every block fails, re-raise the first recoverable exception. Returning an empty success
would hide a broken generation stage.

## 6. Pipeline refactor

File: `src/workflows/pipelines/gen_angles.py`

The model-generation path:

1. derives `retained_target` and `raw_target` from configuration;
2. passes `raw_target` to the backend;
3. applies tangent builder, relevance gate, and deduplication;
4. retains at most `retained_target`;
5. reports target attainment and shortfall.

New report fields:

- `angles_retained_target`;
- `angles_raw_target`;
- `angles_target_reached`;
- `angles_target_shortfall`;
- `raw_target_multiplier`.

The extractive path remains deterministic and bounded by the same retained target.

## 7. Multi-frame compatibility

No codec formula changes are required. Existing multi-frame orchestration continues to use
`recoverable_selection_channel_capacity()` for each carrier.

Regression checks must prove:

- comment choice count is unaffected by angle-input sampling;
- tangent choice count matches the retained post artifact;
- frame boundaries and offsets remain stable;
- partial failure reporting is unchanged.

## 8. Migration

### Existing caches

Content-hash angle caches remain valid. Deterministic sampling simply references a smaller
subset. No cache deletion or migration is required.

### Existing researched posts

They can be reprocessed under the bounded profile. Large JSON artifacts do not need to be
rewritten before angle generation.

### Existing angle artifacts

They retain their original choice counts and codec behavior. Regeneration under the new
profile produces a different dictionary ID and must be treated as a new versioned artifact.

### Running processes

Stop legacy unbounded generation before deploying the refactor. The process cache is
recoverable and should be retained.

## 9. Operational defaults

Recommended normal command environment:

```powershell
$env:WORKFLOW_ENCODING_PROFILE = 'balanced'
$env:WORKFLOW_CAPACITY_LIMITS_ENABLED = '1'
uv run python scripts/run_prep_until_google_quota_then_stego.py ...
```

Recommended high-capacity experiment:

```powershell
$env:WORKFLOW_CAPACITY_PROFILE = 'high'
$env:WORKFLOW_ANGLES_MAX_OUTPUT = '128'
$env:WORKFLOW_ANGLES_RAW_TARGET_MULTIPLIER = '4'
```

The high-capacity override must be reported in benchmark metadata.

## 10. Rollback

Runtime rollback requires no code or data deletion:

```powershell
$env:WORKFLOW_CAPACITY_LIMITS_ENABLED = '0'
```

This restores exhaustive limits for a controlled legacy comparison. It does not bypass
provider quotas or HTTP timeouts.

## 11. Completion checklist

- configuration defaults and reports updated;
- deterministic sampler implemented;
- `max_results` propagated through all ports;
- both angle backends stop early;
- malformed blocks isolated;
- pipeline reports target status;
- targeted tests pass;
- full tests and pyright pass;
- testing plan results recorded.

## 12. Validation record (2026-07-28)

- Focused configuration, sampler, runner, pipeline, artifact, codec, receiver, and
  multi-frame tests passed.
- Full `uv run pytest -q` passed with one pre-existing skip.
- `uv run pyright`, focused Ruff checks, and `git diff --check` passed.
- No workflow/system prompts or codec capacity formulas changed.
- The live bounded GPU pilot and soak experiment remain operational validation tasks; no
  live provider calls were made during this implementation.
