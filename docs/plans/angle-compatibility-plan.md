# Angle Compatibility Plan

## Goal

Reduce structurally doomed stego attempts by identifying when the selected angle is too weak a semantic fit for the post context.

## Hypothesis

Some failures are caused before generation starts: the embedded angle is valid at the codec level but implausible as a natural interpretation of the post and comment chain. In those cases, no prompt tuning will produce a stable, recoverable comment reliably.

## Scope

- Add compatibility checks around selected-angle usability.
- Do not change the codec contract unless a narrower selection policy still preserves sender/receiver symmetry.
- Keep prompt changes out of scope for this branch.

## Planned Changes

1. Define a compatibility score between the selected angle and the post/comment context.
2. Add diagnostics showing whether the selected angle is semantically central, adjacent, or out-of-distribution for the current post.
3. Decide one policy for low-compatibility cases:
   - fail fast with a specific error, or
   - remap within a codec-safe local neighborhood if that preserves the contract.
4. Log shortlist presence of the selected angle during validation to separate prompt drift from impossible recovery cases.
5. Add targeted tests for weak-fit versus strong-fit angle/post pairings.

## Expected Benefits

- Cleaner failure mode when the selected angle is not realistically encodable.
- Less wasted LLM traffic on cases that are unlikely to decode correctly.
- Better observability into whether prompt drift or target incompatibility caused a miss.

## Risks

- Any remapping approach could violate sender/receiver symmetry if not designed carefully.
- Fail-fast behavior may reduce total throughput if many current selections are weak-fit.
- Compatibility scoring may be noisy if based on the same embedding/search stack used for decode.

## Validation

1. Re-run the `1naqplk` case and check whether `idx=59` is flagged as low-compatibility before encode.
2. Confirm strong-fit cases still proceed without extra false positives.
3. Add tests covering fail-fast or remap behavior, depending on the chosen policy.
