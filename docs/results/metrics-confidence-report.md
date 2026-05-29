# Metrics Confidence Report

Generated on 2026-05-11 from saved repo artifacts. This report explains why the current metric set is enough for a narrow, confident claim without immediately adding more samples.

## Bottom Line

The current data is enough to state the `balanced` real-workload baseline confidently.

It is not enough to make a final security-variant ranking across all variants. That would still need a clean, same-post, same-code run with at least `100` successful samples per compared variant.

## Confident Claim

For the `balanced` variant on the repeated overnight real workload:

- Receiver recovery is consistently `1.0` on successful samples.
- Attempt-level success is consistently about `98%`.
- Matched-post JSD is extremely stable at about `0.587`.
- Matched-post KLD is stable at about `9.15` under the repo's current word-unigram `KL(stego || baseline)` metric with `alpha = 1e-6`.
- Hidden expansion is `0.0`; payload is carried through the selection channel.

The KLD value should be reported as an implementation-specific divergence score, not as an absolute naturalness probability. JSD is the more stable headline distance metric.

## Evidence

Five completed overnight `balanced` runs each requested `1000` real-workload samples:

| Run | Requested | Succeeded | Failed | Success | Receiver | KLD | JSD | Global KLD | Global JSD | Perplexity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `overnight_balanced_20260511T033540Z` | 1000 | 980 | 20 | 98.0% | 1.0 | 9.117065 | 0.587139 | 5.436929 | 0.583370 | 181.064 |
| `overnight_balanced_20260511T005031Z` | 1000 | 981 | 19 | 98.1% | 1.0 | 9.158618 | 0.587183 | 5.451137 | 0.583388 | 184.118 |
| `overnight_balanced_20260510T221021Z` | 1000 | 981 | 19 | 98.1% | 1.0 | 9.168399 | 0.587202 | 5.435366 | 0.583382 | 182.638 |
| `overnight_balanced_20260510T064418Z` | 1000 | 978 | 22 | 97.8% | 1.0 | 9.148725 | 0.586957 | 5.442605 | 0.583142 | 184.738 |
| `overnight_balanced_20260510T041410Z` | 1000 | 981 | 19 | 98.1% | 1.0 | 9.158780 | 0.587244 | 5.450886 | 0.583573 | 183.609 |

Aggregate over those five runs:

| Metric | Mean | Min | Max | Std dev |
|---|---:|---:|---:|---:|
| Successful samples per run | 980.2 | 978 | 981 | 1.166190 |
| Matched-post KLD | 9.150317 | 9.117065 | 9.168399 | 0.017752 |
| Matched-post JSD | 0.587145 | 0.586957 | 0.587244 | 0.000100 |
| Global KLD | 5.443385 | 5.435366 | 5.451137 | 0.006678 |
| Perplexity | 183.233408 | 181.064489 | 184.737812 | 1.284138 |

The variance is small enough that adding another identical overnight `balanced` run is unlikely to change the headline conclusion.

## Why This Is Enough

The sample count is already far above the repo's recommended minimum:

- Minimum reportable real comparison: `50` successful samples per variant.
- Recommended real comparison: `100` successful samples per variant.
- Current `balanced` evidence: five runs with `978-981` successful samples each.

The result is also replicated. The important metric is not just `n = 980`; it is that five separate completed runs produce nearly identical KLD/JSD/global KLD/perplexity values.

The only caveat is independence. The overnight runs reuse a fixed pool of real posts, so these runs prove metric stability over repeated generation on that workload. They do not prove generalization to every Reddit/news topic.

## KLD Caveat

The repo computes KLD as word-unigram `KL(stego || baseline)` with additive smoothing `alpha = 1e-6`. That metric is useful for same-alpha comparisons, but the absolute number is sensitive to rare stego-only tokens.

A quick alpha sensitivity check on the latest naturalness-gate run showed:

| Alpha | Primary KLD | Primary JSD |
|---:|---:|---:|
| `0.01` | 2.380131 | 0.282651 |
| `0.001` | 4.778566 | 0.454890 |
| `0.0001` | 5.922274 | 0.539864 |
| `0.00001` | 6.576773 | 0.558304 |
| `0.000001` | 7.153890 | 0.561181 |
| `0.0000001` | 7.720911 | 0.561571 |

That means KLD should not be the only headline metric. Report KLD with its smoothing setting, and pair it with JSD.

## Naturalness-Gate Pilot

The latest `tuned_clean_20260511T115S` run is enough for a directional pilot, not a final claim:

| Variant | Requested | Succeeded | Failed | Success | Receiver | KLD | JSD | Global KLD | Global JSD | Perplexity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `balanced` | 100 | 98 | 2 | 98.0% | 1.0 | 7.261088 | 0.566877 | 4.677223 | 0.562533 | 65.824 |
| `balanced_naturalness_gate` | 100 | 86 | 14 | 86.0% | 1.0 | 7.153890 | 0.561181 | 4.532391 | 0.557291 | 68.699 |

Interpretation: the naturalness gate improves divergence slightly among successful samples, but it reduces attempt-level success in this run. That is promising but not enough for a final recommendation.

## What Not To Claim Yet

Do not claim a final best security variant from the current artifacts.

Reasons:

- Security variants do not all have the same large successful sample count.
- Some previous variant rows were affected by backend failures.
- Some runs are older and may not share identical code/config state.
- The strongest replicated evidence is for `balanced`, not the security variants.

## Recommended Wording

Use this:

> Across five completed real-workload `balanced` runs, each with about `980` successful samples, receiver recovery on successful samples was `1.0` and matched-post JSD was stable at approximately `0.587`. The current KLD implementation reports matched-post KLD around `9.15` with `alpha = 1e-6`; this should be interpreted as a same-metric comparison score rather than an absolute naturalness probability.

Avoid this:

> The system has KLD `9.15`, therefore it is naturally indistinguishable.

## Decision

Do not add more `balanced` samples right now. The existing replicated sample set is enough for the balanced baseline claim.

Add samples only for claims that compare security variants or naturalness-gate variants:

- `100+` successful samples per compared variant for a reportable comparison.
- Same post list for every compared variant.
- Same committed code/config/prompt state.
- Failure classes reported separately from metric scores.
