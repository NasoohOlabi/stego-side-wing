# Results Findings

Generated from existing repo artifacts on 2026-05-01. This is an interpretation of the saved metrics, not a fresh experiment run.

## Executive Summary

The repo has useful metric data, but it is not yet organized as a single final experiment report. Results are split across `metrics/e2e_runs`, `metrics/pareto_runs`, per-variant `summary.json` files, and automation logs.

The strongest current finding is that the pipeline can reliably recover payloads on successful samples, but recent real-workload runs are confounded by infrastructure failures and incomplete runs. Synthetic results look excellent, but they should not be treated as real-world proof.

## What Pareto Search Means Here

Pareto search compares variants across multiple objectives instead of optimizing one metric only.

A variant is Pareto-dominated if another variant is at least as good on every objective and better on at least one. The repo's Pareto objectives include:

- Higher `receiver_success_rate`
- Lower `matched_post_kl`
- Lower `matched_post_jsd`
- Lower `hidden_expansion_ratio`
- Lower `standard_fallback_rate`
- Higher `unique_selection_signatures`

In plain terms: Pareto search tries to find variants that balance reliability, stealth, payload overhead, and diversity.

## Was Pareto Search Successful?

Partially.

Synthetic Pareto screening succeeded technically. Many variants reached:

- `receiver_success_rate = 1.0`
- `matched_post_kl = 0.0`
- `matched_post_jsd = 0.0`
- `200/200` successful samples

However, this is synthetic data. The `0.0` KLD result is useful as a regression/sanity signal, but it is not strong evidence of natural real-world steganographic quality.

Real-workload Pareto screening was mixed. The older real retry run produced usable per-variant results, but newer runs were affected by failures such as:

- `404 Client Error` from the configured LM Studio/ngrok endpoint
- decode validation failures
- incomplete latest Google run with no final summary yet

Conclusion: the search machinery works, but the latest real-world result set is not clean enough to declare a final winner.

## Best KLD Found

KLD means KL divergence, computed as `KL(stego || baseline)` over word unigrams. Lower is better.

The literal best KLD found is:

| Scope | Variant | Samples | KLD | Interpretation |
|---|---:|---:|---:|---|
| Synthetic | multiple variants | `200/200` | `0.0` | Good smoke/regression result, not real-world proof |

The best real-workload matched-post KLD found is:

| Scope | Variant | Samples | KLD | JSD | Receiver Success |
|---|---:|---:|---:|---:|---:|
| Real workload | `balanced` | `33/50` | `6.595933289687123` | `0.5279200411934923` | `1.0` |

Important caveat: `balanced` is not the security profile. It has the best language-match score but is not necessarily the best secure stego variant.

## Best Security Variant Signals

For security-focused variants, the strongest recent clean result is:

| Variant | Samples | KLD | JSD | Global KLD | Perplexity | Receiver Success |
|---|---:|---:|---:|---:|---:|---:|
| `sec_v2_anchored` | `50/50` | `7.482240843164044` | `0.5397898846773577` | `4.834079514792149` | `1.8153538434022538` | `1.0` |

Older smaller-sample security result:

| Variant | Samples | KLD | JSD | Receiver Success | Note |
|---|---:|---:|---:|---:|---|
| `sec_v2_natural_then_anchor_retry` | `25/25` | `7.299420671558595` | `0.5591236249946769` | `1.0` | Better KLD than `sec_v2_anchored`, but only half the sample size |

The latest completed security rating run showed:

| Variant | Samples | KLD | Receiver Success | Note |
|---|---:|---:|---:|---|
| `security_legacy` | `45/50` | `7.662965867205255` | `1.0` | Other variants had `0/50`, mostly due backend/API failure |

Do not interpret the latest `0/50` variant rows as proof those variants are bad. The automation log shows repeated `404 Client Error` from the LLM endpoint.

## Most Recent Variant Features

The current variant manifest is `config/pareto_variants.json`.

Main variants/features:

- `balanced`: frozen natural/model baseline
- `capacity`: frozen high-capacity baseline
- `security_legacy`: security baseline using `hmac_xor_v1` and anchored prompting
- `sec_v2_anchored`: `secure_compact_v2` with anchored prompting
- `sec_v2_guided_natural`: `secure_compact_v2` with guided-natural prompting
- `sec_v2_natural_then_anchor_retry`: guided-natural first pass, anchored retries after decode misses
- `sec_v2_guided_natural_hybrid_extract`: guided-natural with hybrid extractive/model carrier selection
- `sec_v2_natural_then_anchor_retry_hybrid_extract`: retry-aware guided-natural with hybrid extractive/model carrier selection

Latest git context observed:

- Current branch: `codex/configurable`
- Latest commit: `de05058 Ignore generated data files`
- There are local modified source files, so repo state may not exactly match committed code.

## Metric Glossary

| Metric | Meaning | Better Direction |
|---|---|---:|
| `receiver_success_rate` | Fraction of successful samples where receiver recovered the payload | Higher |
| `matched_post_kl` | Word distribution distance between stego text and matched source post comments | Lower |
| `matched_post_jsd` | Symmetric divergence against matched post comments | Lower |
| `global_corpus_kl` | Divergence against the full dataset comment corpus | Lower |
| `perplexity` | Language-model fluency proxy | Usually lower, but compare carefully |
| `hidden_expansion_ratio` | Hidden carrier bytes divided by original payload bytes | Lower |
| `standard_fallback_rate` | Rate of fallback compression/carrier method | Lower |
| `bps_total` | Total hidden bits per stego byte | Higher for capacity |
| `unique_selection_signatures` | Diversity of selected carrier positions | Higher |

## Is The Sample Size Enough?

Short answer: not yet for final claims.

The sample size is enough for smoke testing and directional comparison, but not enough for a strong final research claim.

Current sample-size quality:

| Sample Set | Size | Confidence |
|---|---:|---|
| Synthetic screens | `200` per variant/payload size | Good for regression and pipeline sanity |
| Older real retry run | `25` per variant | Directional only |
| Recent real runs | up to `50` requested per variant | Better, but many failed or incomplete |
| Latest Google run | incomplete/no final summary | Not usable yet |

Recommended standard:

- Minimum useful real comparison: `50` successful samples per variant
- Better thesis/report standard: `100` successful samples per variant
- Stronger claim: `200+` successful samples per variant across multiple post categories
- Always report failures separately from metric scores

Why this matters:

- KLD/JSD can vary by post topic and comment style.
- Failed samples bias metrics because only successes are scored.
- Infrastructure failures should be excluded or labeled separately.
- `25` samples can identify bad variants, but it is weak for ranking close variants.

## Current Interpretation

Best language-match result: `balanced`, KLD `6.5959`, but not the best security answer.

Best currently defensible security candidate: `sec_v2_anchored`, because it has `50/50` success and complete metrics.

Most promising but needs rerun: `sec_v2_natural_then_anchor_retry`, because it had better KLD in the older `25/25` run but weak/incomplete later evidence.

Least trustworthy data: latest runs where failures were caused by LLM endpoint errors.

## Recommended Next Steps

1. Create one canonical results dashboard or generated Markdown report under `metrics/INSIGHTS.md`.
2. Re-run real workload with a stable LLM backend and no ngrok 404 failures.
3. Require at least `50` successful samples per variant before ranking variants.
4. Prefer `100+` successful samples per variant before making final claims.
5. Separate failure classes: infrastructure, generation failure, decode failure, and metric failure.
6. Add confidence columns: requested samples, successful samples, failed samples, and failure reason counts.
7. Treat synthetic `KLD = 0.0` as a sanity check, not the headline result.
