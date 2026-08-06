# Context-Conditioned Weighted Angle Sampler Implementation Plan

This plan implements
`context-conditioned-weighted-angle-sampler-spec.md` without changing prompts or the
mathematical definition of recoverable selection capacity.

## 1. Refactor boundaries

In scope:

- parent-aware context models;
- post-body validation;
- relationship-aware comment selection;
- deterministic research relevance ranking;
- weighted source allocation;
- parent-conditioned artifact identity and caching;
- two-stage frame planning;
- sender/receiver parity checks;
- migration-safe feature gating;
- tests and operational documentation.

Out of scope:

- workflow/system prompt changes;
- live search during sender or receiver execution;
- eager codebook generation for every parent;
- codec width-formula changes;
- invisible or non-rendering carriers.

## 2. Phase 0: contract fixtures

Before implementation, add frozen fixtures for:

- a post-root selection;
- a deeply nested selected parent;
- a selected parent with many siblings;
- a parent with no siblings but several children;
- missing and malformed parent IDs;
- a frozen research pool with relevant and irrelevant snippets.

Record current parent choice counts and post-level codec behavior. These fixtures establish
that the new sampler cannot shrink or reorder the parent channel.

## 3. Phase 1: shared artifact identity

Files:

- `src/workflows/utils/angle_artifact.py`
- `src/workflows/utils/prep_run_manifest.py`
- relevant artifact schemas and tests

Actions:

1. Add the `context_weighted_v2` sampler identity.
2. Define a parent-conditioned artifact model rather than loose dictionaries.
3. Include parent ID, frozen research hash, relationship counts, allocation weights,
   dictionary ID, tangent hash, and recoverable capacity.
4. Keep schema-v1/v2 post-level artifact readers unchanged.
5. Reject mixed post-level and context-conditioned artifacts in one generation lane.

## 4. Phase 2: context graph helpers

Suggested file:

```text
src/workflows/utils/comment_context.py
```

Introduce pure helpers for:

- indexing the flattened comment tree by comment ID;
- resolving parent and ancestor chains;
- finding siblings by immediate parent;
- finding direct children;
- building the root-parent candidate set;
- normalizing and deduplicating visible comment text;
- classifying each candidate relationship.

The helpers must not mutate the post or depend on filesystem order, Python hashes, network
services, or an LLM.

Malformed duplicate comment IDs fail validation rather than choosing one arbitrarily.

## 5. Phase 3: deterministic research ranking

Suggested file:

```text
src/workflows/utils/context_research.py
```

Implement a pure lexical ranker over the frozen research pool:

1. build a query document from post title/body, selected parent, and nearest ancestors;
2. normalize visible text;
3. score term overlap with length normalization;
4. use stable SHA-256 ranking as the final tie-breaker;
5. retain source ID, URL, text hash, and score in the report.

Do not fetch, generate new queries, or call an embedding service. A future persisted-vector
ranker requires a new ranker and sampler version.

## 6. Phase 4: weighted sampler

File:

```text
src/workflows/utils/text_utils.py
```

Prefer moving new orchestration into:

```text
src/workflows/utils/context_sampler.py
```

Implement pure functions for:

- mandatory post/parent selection;
- capped ancestor selection;
- deterministic sibling/child/fallback ranking;
- source-budget enforcement;
- weighted schedule construction;
- exhausted-source redistribution;
- dictionary metadata and ID construction.

Default schedule:

```text
comment, comment, comment, research
```

The post body is inserted before the weighted schedule. The selected parent and ancestors
consume comment slots before siblings, children, or global fallback.

Feature-gate this path behind:

```text
WORKFLOW_CONTEXT_SAMPLER=context_weighted_v2
```

The default remains `post_level_v1` until sender/receiver parity and efficiency gates pass.

## 7. Phase 5: parent-conditioned angle generation

Files:

- `src/workflows/pipelines/gen_angles.py`
- `src/workflows/ports.py`
- backend adapters/services only if a typed parent-context request is required

Actions:

1. Add `selected_parent_id` to the angle-preview contract.
2. Build the context dictionary using the selected parent.
3. Preserve current raw-target stopping and malformed-block isolation.
4. Persist parent-conditioned dictionary and tangent identities.
5. Cache by parent-context identity.
6. Keep post-level generation behavior unchanged behind the feature flag.

Do not concatenate the parent text into an LLM prompt outside the normal dictionary input;
this phase does not authorize prompt-template edits.

## 8. Phase 6: streaming frame planning

Files:

- `src/workflows/pipelines/stego_multiframe.py`
- `src/workflows/pipelines/stego.py`
- shared codec helpers only where orchestration needs a pure operation

Refactor frame planning:

1. compute recoverable parent width from the unchanged comment tree;
2. consume parent bits and resolve the parent;
3. request/load that parent's verified tangent codebook;
4. compute its recoverable tangent width;
5. consume only that many tangent bits;
6. record the exact bit offset and parent/context identity;
7. continue to the next frame without speculative capacity.

Do not count a frame until its parent-conditioned tangent codebook exists and is verified.

This phase must retain the legacy planner for post-level artifacts.

## 9. Phase 7: receiver alignment

Files:

- `src/workflows/pipelines/receiver.py`
- receiver frame helpers and tests

Change receiver ordering:

1. locate the sender comment and observe its parent ID;
2. rebuild the pre-sender post and frozen research pool;
3. pass the observed parent ID into context-conditioned angle generation;
4. verify dictionary ID, sampler version, and tangent hash;
5. decode the tangent index;
6. preserve partial-failure offsets.

An unknown parent or identity mismatch is a failed frame, not a request to regenerate a
post-level codebook.

## 10. Phase 8: configuration and observability

File:

```text
src/infrastructure/config.py
```

Add typed getters for:

- sampler mode;
- comment/research weights;
- maximum ancestors;
- child inclusion;
- global fallback.

Reports and JSONL logs add:

- selected parent ID;
- relationship counts;
- requested and effective allocations;
- source exhaustion and redistributed slots;
- research availability;
- dictionary/cache identity;
- parent and tangent recoverable widths;
- context-build and angle-generation elapsed time.

Invalid settings use documented safe fallbacks and emit structured warnings.

## 11. Phase 9: migration and artifact separation

- Existing `stable_round_robin_v1` angle posts remain readable.
- Existing content-hash block caches remain valid.
- New parent-context caches use a separate namespace.
- New sample runs use isolated dataset roots.
- Workload runners reject mixed sampler versions within one lane.
- No historical artifact is rewritten in place.

Rollback:

```powershell
$env:WORKFLOW_CONTEXT_SAMPLER = 'post_level_v1'
```

## 12. Unit tests

### Context graph

- root-parent neighborhood;
- ancestor order;
- sibling membership;
- child membership;
- duplicate/missing IDs;
- input reorder independence.

### Weighted allocation

- post body always first;
- missing body fails;
- selected parent retained;
- tight ten-block budget is comment-heavy;
- source caps and global cap hold;
- exhausted sources redistribute slots;
- optional children/fallback disable cleanly;
- repeated calls produce the same dictionary ID.

### Research

- selected-parent relevance changes ranking;
- no network or LLM dependency;
- stable ties;
- missing research degrades to comments;
- frozen research hash is recorded.

### Capacity

- parent choice count is unchanged;
- parent bits are consumed before tangent capacity is known;
- different parents may produce different tangent widths;
- frame offsets remain contiguous;
- a failed middle frame does not shift later offsets.

## 13. Sender/receiver regression gate

Run:

```powershell
uv run pytest -q `
  src/tests/test_stego_codec.py `
  src/tests/test_pipeline_stego.py `
  src/tests/test_receiver_pipeline.py `
  src/tests/test_stego_roundtrip_golden.py
```

Add an offline payload recovery test spanning at least three frames with three distinct
selected parents. Assert exact payload recovery and matching context/tangent hashes.

## 14. Efficiency pilot

Use a frozen set of at least 25 posts with small, medium, large, and very large comment
trees. Compare:

| Arm | Sampler | Comment:research weight |
|---|---|---:|
| A | `post_level_v1` | 1:1 |
| B | `context_weighted_v2` | 3:1 |
| C | `context_weighted_v2` | 4:1 |

Measure:

- exact frame recovery;
- verified bits per GPU-minute;
- tangent target attainment;
- selected-parent relevance;
- cache reuse;
- comment relationship mixture;
- naturalness and synthetic detection;
- failures by stage.

Advance the smallest comment-heavy configuration that preserves exact recovery and improves
selected-parent relevance without a material capacity loss.

## 15. Validation gate

Before enabling by default:

```powershell
uv run pytest -q
uv run pyright
uv run ruff check src scripts
```

Then run:

1. one offline three-parent multi-frame recovery;
2. one bounded live GPU pilot;
3. one interrupted/restarted cache-reuse test;
4. one receiver parity test from a separately reconstructed context.

Record environment, sampler version, model, configuration, artifact paths, elapsed time,
and cache status.

## 16. Completion checklist

- artifact model and version added;
- comment-context helpers implemented;
- deterministic frozen-research ranker implemented;
- weighted sampler implemented;
- post body and parent invariants enforced;
- two-stage frame planner implemented;
- receiver observes parent before rebuilding angles;
- cache namespace and migration documented;
- mixed versions rejected;
- targeted and full tests pass;
- offline three-parent recovery passes;
- live bounded pilot recorded;
- default remains gated until pilot acceptance.
