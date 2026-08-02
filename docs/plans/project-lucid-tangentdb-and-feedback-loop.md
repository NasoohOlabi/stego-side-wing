# Project LUCID: Tangent Distinctness and Honest Decode Recovery

**LUCID** = **L**inguistic **U**niqueness, **C**ontextual **I**ntegrity, and
**D**ecodable selection.

## Decision

Project LUCID replaces forced recoverability with two upstream safeguards:

1. build a TangentsDB whose entries are distinct, thread-grounded, and
   decodable from natural replies; and
2. use an LLM-assisted feedback loop to improve a failing natural candidate
   without ever injecting a synthetic carrier.

If those safeguards cannot yield an exact decode within a bounded budget, the
encode attempt fails honestly.

No implementation is authorized by this plan. Prompt edits require the
separate double-confirmation process in `AGENTS.md`.

## Non-negotiable invariants

- The visible carrier is an LLM-produced reply, never a deterministic template.
- Sender and receiver use one versioned TangentsDB and one codec contract.
- Every accepted output has recorded candidate provenance, prompt hashes,
  model identity, decode outcome, and quality-gate outcome.
- Exact recovery is necessary but not sufficient: thread fit and naturalness
  remain hard acceptance conditions.
- Exhaustion produces a failed attempt with diagnostics, never a fabricated
  success.

## Workstream A — TangentsDB distinctness

### A1. Define distinctness before generation

Specify a TangentsDB entry as a compact, reply-expressible semantic intent:
one subject/frame, one stance or causal relation, and one thread-grounded cue.
Reject entries that are merely category relabels, generic analysis tasks, or
phrases that cannot be spoken naturally in the selected reply context.

### A2. Generate a candidate pool, then select a codebook

Generate more angle candidates than needed. Score each candidate for:

- grounding in the post and selected parent comment;
- semantic separation from every retained angle;
- lexical overlap risk with neighboring angles;
- reply realizability by a small generation probe; and
- receiver confusion risk using pairwise decode probes.

Select the final fixed-size codebook globally rather than accepting angles in
generation order. The selection objective should maximize the minimum
pairwise separation while preserving category and source diversity.

### A3. Use an LLM only as a structured critic, not as hidden repair

An LLM critic may identify overlapping entries and propose replacements. Its
output must be structured, versioned, and independently checked by
deterministic similarity and probe-decode measures. It may change the
TangentsDB before generation; it may not rewrite an already-generated carrier
outside the normal feedback loop.

### A4. Version and freeze the result

Persist the full TangentsDB, generation inputs, selected-parent context,
candidate scores, rejection reasons, prompt hashes, model identity, and a
content hash. Sender and receiver must reference that frozen artifact.

## Workstream B — Helpful, bounded feedback loop

### B1. Diagnose the failure

For each natural candidate, record whether failure is due to no decode,
wrong-angle decode, weak thread grounding, unsupported detail, or a quality
gate violation. Do not expose the payload bits to the LLM.

### B2. Request an LLM revision with useful constraints

The revision input should include the original candidate, selected parent
comment, post context, selected angle as a compact semantic goal, and concise
failure feedback. It should ask for a fresh natural reply that directly
addresses the parent comment while making the selected angle clearer through
ordinary wording. It must forbid copying source quotes, angle labels, and
decoder-oriented boilerplate.

Prompt design will be proposed and reviewed separately; this plan does not
modify any prompt text.

### B3. Revalidate from scratch

Every revision goes through the same context, naturalness, and receiver-decode
checks as an initial candidate. The decoder receives no special handling for
revisions. Retain all attempts and outcomes.

### B4. Stop honestly

Use a small, configurable retry budget. On exhaustion, return a typed encode
failure containing the selected angle ID, failure taxonomy, candidate
provenance, and safe diagnostics. Count it as a failure in all evaluations.

## Refactoring sequence

1. Specify TangentsDB schemas, distinctness metrics, codebook selection, and
   artifact versioning.
2. Add pure deterministic scoring and tests for duplicate, adjacent, generic,
   and non-reply-expressible tangents.
3. Add the LLM structured-critic interface behind the new artifact contract.
4. Add sender/receiver compatibility checks for the frozen codebook.
5. Design and obtain confirmation for feedback-loop prompts.
6. Implement bounded revision orchestration with attempt provenance.
7. Add failure taxonomy, dashboard rendering, and benchmark accounting.
8. Run a frozen-manifest pilot, inspect failures manually, then gate any
   broader benchmark run on predefined recovery and naturalness thresholds.

## Acceptance criteria

- No code path can emit a visible carrier that did not come from an LLM call.
- TangentsDB builds report pairwise separation and reject ambiguous entries.
- A held-out probe set demonstrates materially lower angle-confusion than the
  current generator at equal codebook size.
- Failed decode attempts remain visible in artifacts and metrics.
- Every accepted carrier has exact receiver recovery and passes the existing
  contextuality/naturalness gates.
- A reviewer can reproduce an output from its frozen TangentsDB and recorded
  LLM provenance without relying on process memory.

## Risks to manage

- Greater semantic separation can reduce the number of usable angles and thus
  capacity; report that trade-off explicitly.
- LLM critics can introduce new generic or overly academic language; retain
  deterministic filters and probe decoding.
- Feedback loops can overfit to the decoder; use held-out receiver probes and
  human-facing quality gates.
- Prompt changes are high-risk and require explicit review before any use.
