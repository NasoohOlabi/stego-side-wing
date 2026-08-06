# ZLG benchmark audit — 2026-07-26

> **Correction 2026-07-27 — capacity rows only.** The `Ours` capacity figures below are read
> from `paired_rows.jsonl`, whose enrichment step dropped the angle/tangent channel
> (`tangent_choices: 0` on all 304 rows). Corrected post-clustered recoverable capacity is
> **18.66 bits/comment, not 10.66**; ZLG's 76.70 is unaffected, so the ratio is ≈4.1x rather
> than ≈7.2x. The rows marked †below are affected. No quality, detection, or judge metric
> changes. See [`zlg-sample-audit-2026-07-27.md`](zlg-sample-audit-2026-07-27.md) for the
> defect, the recomputation, and the fix.

## Scope and inference unit

The audited run is `metrics/zlg_comparison_runs/zlg_demo_20260712_v6_200`.
It contains 304 accepted row pairs but only 47 independent post clusters. Our
304 rows contain 47 unique texts, whereas the ZLG arm generated a distinct
output per accepted trial. Primary descriptive means and sign tests therefore
aggregate within post first and give each of the 47 posts equal weight.

Objective paired p-values below use exact two-sided sign tests and Holm
correction across the objective-metric family. Judge p-values are reported
separately and are not part of that correction family.

## Corrected results

| Metric | Ours | ZLG | Correct interpretation |
| --- | ---: | ---: | --- |
| Recoverable selection capacity, bits/comment † | 10.66 (corrected: 18.66) | 76.70 | ZLG wins; its value is conditional on a successful hide |
| ZLG failure-adjusted payload, bits/attempt | n/a | 41.77 | 304/547 attempts were accepted and decode-verified (55.6%) |
| GPT-2 perplexity, arithmetic post mean | 91.52 | 34.66 | ZLG wins; ours also has a heavy upper tail |
| KL(stego‖matched-post), nats | 7.987 | 5.266 | ZLG wins, but length/sparsity confound the construct |
| JSD(stego, matched-post), nats | 0.549 | 0.484 | ZLG wins, with the same confound |
| KL(stego‖global-corpus), nats | 4.764 | 3.517 | ZLG wins; corpus includes evaluation posts |
| JSD(stego, global-corpus), nats | 0.549 | 0.491 | ZLG wins |
| Repeated word-type share | 0.045 | 0.234 | Ours lower on all 47 posts; Holm p < 3e-13, length-confounded |
| BLEU | 1.126 | 1.187 | No clear difference; raw p=0.883 |
| ROUGE-1 F1 | 0.092 | 0.126 | No clear difference after correction |
| ROUGE-2 F1 | 0.003 | 0.010 | ZLG wins after correction |
| ROUGE-L F1 | 0.065 | 0.089 | No clear difference after correction |
| BERTScore precision | 0.8369 | 0.8278 | Ours wins 33/47; Holm p=0.0477 |
| BERTScore recall | 0.8442 | 0.8461 | No clear difference |
| BERTScore F1 | 0.8404 | 0.8367 | Ours is directionally higher; Holm p=0.160 |
| Same-post self-consistency | 1.000 | 0.270 | Ours has effectively zero output diversity; this is adverse, not a win |
| Thread-grounded factuality, 1–5 | 1.766 | 1.527 | Ours +0.239, but sign-test p=0.132 |
| G-Eval overall, 1–5 | 1.739 | 1.862 | ZLG wins, unadjusted judge p=0.0137 |
| Synthetic detection rate | 23.9% | 41.2% | Ours is harder to spot; 47-post sign-test p=0.00948 |
| Exploratory lexical-quality index, 0–100 | 99.04 | 92.11 | Ours wins; Holm p < 1e-11 |

M1 human-likeness A/B, M3 thread relevance, and M4 writing quality were not run
in this artifact and must remain `n/a`. M5 is explicitly exploratory: it is a
handcrafted MATTR/bigram/length index, not a validated human-quality endpoint.

## Calculation audit

### Capacity

The old dashboard counted physical bit widths based on `ceil(log2(N))` even
when modulo mapping made multiple bit patterns select the same state. Such
aliases are not uniquely recoverable. The corrected capacity is:

`floor(log2(comment choices)) + floor(log2(angle choices))`

for the current separately encoded comment and angle decisions. † The 10.66
figure in the table above evaluates only the first term, because the artifact
it was read from lost the angle channel; applying the formula as written gives
18.66. The formula itself is unchanged. Future
artifacts now preserve both physical width and recoverable bits. ZLG's 76.70
bits is hide/reveal verified but conditions on successful attempts. Reporting
41.77 recovered payload bits per attempted run exposes the 44.4% failure rate.
The two arms still have unequal trial structures, so the benchmark should be
rerun symmetrically before making a definitive throughput claim.

### Perplexity

The current local implementation computes causal-LM token NLL correctly and
scores each token once. The arithmetic mean of per-text perplexities is
heavy-tail sensitive; median and token-weighted corpus perplexity should be
co-headlines in the next run. GPT-2 predictability is not human-likeness and
should remain labelled a proxy.

### KL and JSD

Both formulas are normalized additive-smoothed unigram divergences over the
union vocabulary and use natural logarithms. JSD is correctly bounded by
`ln(2)`. They compare one short generated comment with a pooled post/corpus
distribution. Our outputs average about 34 words versus about 51 for ZLG, so
vocabulary sparsity and length materially favor ZLG. The global corpus also
contains evaluation posts. The next run should add equal-length truncation,
held-out corpus data, alpha sensitivity, and a human-control-normalized gap.

### BLEU, ROUGE, and BERTScore

BLEU is sentence SacreBLEU; ROUGE values are stemmed F-measures; BERTScore is
computed with the stored `roberta-large` provenance. The reference is the
first non-empty human comment, which is arbitrary for an open-ended Reddit
reply. These are overlap/semantic-reference proxies, not direct naturalness
scores. A stale `summary.json` previously showed obsolete MiniLM BERTScore
values (~0.24) even though `paired_rows.jsonl` contained current RoBERTa values
(~0.84). The enrichment script now refreshes the summary atomically.

### Self-consistency

The old implementation removed every alternative text equal to the candidate,
making our repeated outputs appear `n/a`. It now removes exactly the one
self-row and retains additional identical outputs as cosine similarity 1.
The corrected result exposes a major weakness: only 47 unique our-method texts
across 304 rows.

### Judge metrics

Factuality and G-Eval are cached single-model judge scores. Their inference
correctly aggregates by post, but the dashboard previously displayed row means
beside cluster p-values. It now uses equal-post method means. Factuality is a
thread-evidence score, not canonical Wikipedia-backed FActScore. The next
benchmark should use randomized paired order, at least one independent judge,
and predeclared judge endpoints.

### Synthetic detection

The displayed rates were already equal-post means, but the dashboard attached
the row-level 304-pair McNemar p-value to them. It now uses the existing
47-post clustered artifact: mean ZLG-minus-ours detection delta 0.173,
bootstrap 95% CI [0.064, 0.282], exact sign-test p=0.00948.

### Lexical index

The old metadata documented weights that did not match the v2 implementation.
Rows are now recalculated using 55% moving-average TTR, 30% bigram
non-repetition, and 15% length sanity. Repetition is no longer counted twice.
The index is useful diagnostically but remains exploratory.

## Why our method loses the main capacity/fluency/topical metrics

1. The method has a fundamental one-comment selection ceiling: one comment
   choice plus one tangent choice carries roughly the log of the number of
   available states. ZLG distributes payload across many generated tokens.
2. The benchmark's our-method arm repeats one output many times, providing no
   same-post diversity and preventing selection among alternative natural
   covers.
3. Tangent selection can deliberately move away from the dominant thread
   vocabulary, increasing KL/JSD and lowering G-Eval relevance.
4. Our texts are shorter, which penalizes sparse unigram overlap against pooled
   post/corpus distributions.
5. Historic remote samples also contain templated persona phrasing and
   mojibake; both are plausible perplexity/detection failure modes, though the
   remote checkout predates this benchmark.

## Recommended next experiments

1. Run a fresh symmetric benchmark with equal attempted payload, exact blind
   decode, failures counted as zero, and one independent output per trial.
2. Add a reject/regenerate gate for mojibake and template/persona artifacts.
   Prompt edits require the repository's explicit prompt-change approval.
3. Add length-matched KL/JSD, held-out human controls, median/corpus
   perplexity, randomized paired human-likeness, and replicated judges.
4. Measure a capacity–quality Pareto frontier rather than declaring one global
   winner.
5. To approach ZLG capacity, add multiple independent visible linguistic
   decisions or multi-comment framing; tuning a single comment/angle choice
   cannot remove the architectural ceiling.

