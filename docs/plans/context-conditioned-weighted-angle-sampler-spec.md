# Context-Conditioned Weighted Angle Sampler Specification

Status: proposed

## 1. Objective

Generate each tangent codebook from material that is relevant to the parent comment selected
by the payload, while preserving the visible parent-selection channel and exact
sender/receiver recovery.

The sampler operates on angle-generation inputs only. It must not remove or reorder parent
choices in the steganographic codec.

## 2. Terminology

- **Selected parent**: the post root or existing comment to which the sender reply attaches.
- **Local comment context**: the selected parent, its ancestors, siblings, and optionally its
  direct children.
- **Frozen research pool**: research snippets already persisted in the pre-sender post
  artifact. No live search occurs during frame encoding or receiver reconstruction.
- **Context dictionary**: the bounded input blocks selected for one post and selected parent.
- **Tangent codebook**: the verified, deduplicated angles generated from that dictionary.

## 3. Required invariants

1. A non-empty post body is required and is always context entry zero.
2. The complete visible comment tree remains the parent-choice set.
3. Parent selection happens before context sampling.
4. Sampling never performs live network research.
5. The same post, selected parent, frozen research pool, configuration, and sampler version
   produce the same ordered context dictionary and dictionary ID.
6. Sender and receiver use the same parent-conditioned tangent codebook.
7. Capacity is calculated only after the parent-conditioned tangent codebook is verified.
8. A context or tangent mismatch fails the frame; it must not silently fall back to a
   post-level codebook.

## 4. Two-stage channel

For each frame:

```text
comment_bits = floor(log2(parent_choices))
selected_parent = decode(comment_bits)

context_dictionary = sample(post, selected_parent, frozen_research)
tangent_codebook = build_and_verify(context_dictionary)
tangent_bits = floor(log2(len(tangent_codebook)))

frame_capacity = comment_bits + tangent_bits
```

This avoids a circular dependency: the fixed-width parent channel consumes its bits first,
then the selected parent determines the tangent channel and its remaining capacity.

Frame planning must therefore be streaming rather than assuming one post-level tangent count.
It reads enough payload bits to select the parent, resolves that parent's tangent codebook,
then consumes at most its recoverable tangent width.

## 5. Context selection

### 5.1 Mandatory entries

The sampler first retains:

1. the post body;
2. the selected parent comment, when the parent is a comment;
3. nearest ancestors, subject to the global comment budget.

For a post-root selection, the root is represented by the post body and top-level comments
form the local candidate set.

If the post body is empty, preparation fails before angle generation. A title must not be
silently substituted for the required body.

### 5.2 Local comment candidates

After mandatory entries, comment candidates are considered in this order:

1. siblings sharing the same immediate parent;
2. remaining ancestors, nearest first;
3. direct children of the selected parent;
4. other comments as deterministic fallback only when the local neighborhood cannot fill
   its allocation.

Within a tier, entries are ranked by a stable SHA-256 key over:

```text
sampler version
post ID
selected parent ID
relationship tier
comment ID
normalized visible text
```

Duplicate comment IDs and duplicate normalized text are retained only once.

### 5.3 Research candidates

Research is selected only from the frozen research pool. Each snippet is ranked against a
query document composed of:

- post title and body;
- selected parent text;
- nearest ancestor text.

Ranking must be deterministic. The initial implementation may use a pure lexical relevance
score with a stable SHA-256 tie-breaker. Model embeddings or live services are not permitted
in the sampler contract unless their exact versioned vectors are persisted in the artifact.

### 5.4 Weighted allocation

The post body is outside the weighted budget. Mandatory parent/ancestor entries consume the
comment allocation first.

Default remaining-source weights:

```text
local/fallback comments: 3
research snippets:       1
```

The global budget is filled with the repeating schedule:

```text
comment, comment, comment, research
```

The schedule starts with comments. Exhausted or capped sources are skipped and the remaining
source fills unused slots. Source-specific caps remain hard ceilings.

Default bounded profiles retain their existing total limits:

| Profile | Search cap | Comment cap | Global blocks |
|---|---:|---:|---:|
| low | 8 | 24 | 32 |
| mid | 16 | 48 | 64 |
| high | 32 | 96 | 128 |

Because the post body occupies one block, the mid profile normally has 63 weighted slots.

## 6. Root-parent behavior

When the payload selects the post root:

- the post body remains mandatory;
- top-level comments are treated as siblings;
- research relevance uses the post title/body without selected-comment text;
- deeper comments are deterministic fallback.

Root-parent behavior must be versioned and tested separately from comment-parent behavior.

## 7. Artifacts and identity

The sampler version is:

```text
context_weighted_v2
```

A parent-conditioned artifact records:

- schema and generator version;
- sampler version;
- post ID and selected parent ID (`null` for root);
- global and per-source budgets;
- source weights and allocation schedule;
- relationship counts before and after selection;
- ordered source identifiers and text hashes;
- frozen research-pool hash;
- context dictionary ID;
- raw, relevant, deduplicated, and retained tangent counts;
- final tangent hash and recoverable widths.

The context dictionary ID is the SHA-256 hash of canonical JSON containing the sampler
version, parent ID, effective configuration, and ordered selected-entry identities.

Post-level `stable_round_robin_v1` artifacts remain readable but are not interchangeable
with `context_weighted_v2` artifacts.

## 8. Sender/receiver contract

The sender:

1. consumes the frame's recoverable parent bits;
2. resolves the selected parent ID;
3. builds or loads the matching context dictionary and tangent codebook;
4. verifies and records their IDs;
5. consumes only the recoverable tangent bits;
6. persists the parent/context/tangent identity in frame audit data.

The receiver:

1. observes the sender comment's parent ID;
2. rebuilds the pre-sender post;
3. selects the same frozen research and local comment inputs for that parent;
4. rebuilds or loads the tangent codebook;
5. verifies dictionary and tangent hashes;
6. decodes the tangent index;
7. rejects the frame on any identity mismatch.

Later frames keep their original payload offsets when an earlier frame fails.

## 9. Caching

Angle caches remain content-hash based. A parent-context cache key additionally includes:

- sampler version;
- post ID;
- selected parent ID;
- context dictionary ID;
- tangent-builder configuration hash.

Existing per-block caches may be reused when selected text blocks are unchanged. Existing
post-level artifacts require no migration.

## 10. Configuration

Proposed settings:

| Variable | Default | Meaning |
|---|---:|---|
| `WORKFLOW_CONTEXT_SAMPLER` | `post_level_v1` | Enables the new sampler with `context_weighted_v2` |
| `WORKFLOW_CONTEXT_COMMENT_WEIGHT` | `3` | Comment slots per weighted cycle |
| `WORKFLOW_CONTEXT_RESEARCH_WEIGHT` | `1` | Research slots per weighted cycle |
| `WORKFLOW_CONTEXT_MAX_ANCESTORS` | `8` | Maximum retained ancestor comments |
| `WORKFLOW_CONTEXT_INCLUDE_CHILDREN` | `1` | Allows direct-child candidates |
| `WORKFLOW_CONTEXT_GLOBAL_FALLBACK` | `1` | Allows non-local comments to fill unused slots |

Invalid weights clamp to at least one. Explicit zero for optional children/fallback disables
that source. Effective values are persisted in every artifact.

## 11. Failure semantics

- Missing post body: fail preparation.
- Selected parent missing from the pre-sender tree: fail the frame.
- Frozen research pool missing: continue with comments and record research unavailability.
- Insufficient local comments: use deterministic fallback if enabled.
- No verified tangent: fail the frame.
- Sender/receiver dictionary or tangent mismatch: fail the frame.
- Provider, quota, transport, and authentication errors retain current fatal semantics.

## 12. Acceptance criteria

1. The post body is always first and missing bodies fail.
2. Every parent remains selectable regardless of angle-input budgets.
3. Comment inputs are dominated by the selected parent's ancestors and siblings.
4. Research snippets are deterministically relevant to the selected parent and post.
5. A tight ten-block budget produces a comment-heavy allocation rather than a 1:1 split.
6. Root and comment parents produce deterministic but distinct dictionary IDs.
7. Parent-conditioned tangent counts drive frame capacity without shifting payload offsets.
8. Sender and receiver recover payloads spanning at least three differently-parented frames.
9. Legacy post-level artifacts remain readable and are never mixed with v2 artifacts.
10. Full pytest, Pyright, and an offline end-to-end context-parity test pass.

## 13. Out of scope

- live research during encoding or decoding;
- changing the parent-selection formula;
- changing workflow/system prompts;
- embedding parent IDs or codebook data in invisible text;
- pre-generating tangent codebooks for every comment in a large thread;
- using raw or unverified model outputs as recoverable capacity.
