# Plan: Method-Comparison Metrics v2 — Measuring the "Genuine/Human" Vibe

Status: proposed
Owner: (you)
Related plans: [[tangent-db-revamp]], [[prepared-posts-separate-persistence]]

## 1. Why

The current ZLG comparison ranks ZLG ahead on three metrics — GPT-2 **perplexity**,
**KL** and **JSD** vs the matched post/global corpus — and this reads as "our method
lost on all fronts." It is misleading:

- Those three metrics reward **topical word-overlap and predictability**, not genuine
  human authorship. ZLG completes from the thread, so it parrots thread vocabulary
  (low KL/JSD) and stays predictable (low perplexity). Human writing is often
  *surprising* (higher perplexity by construction). Low perplexity is a known-poor
  proxy for "human-written."
- The one metric that actually models a reader — the **sus-detection** LLM judge that
  hides the comment among 9 real replies — already favors our method (23.7% vs 40.5%
  detection; McNemar p≈2.4e-5; clustered-by-post sign test p≈0.009).
- Objective lexical-quality signals we already log also favor us: unique-token ratio
  0.96 vs 0.77, repetition ratio 0.04 vs 0.23, max-bigram-repeat 1.03 vs 1.66.

So the goal here is not to cherry-pick a winner but to **measure the right things and
report them honestly**: decompose "naturalness" into separable axes, add a direct
human-likeness test, and characterize the tangent DB the revamp is changing.

Also fix a **data artifact** first: in `zlg_demo_20260712_v6_200`, **45 of 47 posts
have a single unique our-method comment repeated across all their samples** — our
diversity is untested, and 304 "pairs" are really 47 independent comments. Any v2
comparison must regenerate so each payload yields a distinct comment, and must report
at the **post cluster** level.

## 2. What is measured today (ground truth)

`scripts/build_zlg_method_comparison_dataset.py`:
- `_quality(text)` (lines 58–70): `word_count`, `unique_token_ratio`,
  `repetition_ratio`, `single_token_share`, `max_bigram_repeat`, `char_count`,
  `has_non_ascii`.
- `_metric_block()` (lines 73–84): `perplexity_gpt2`, `kl_matched_post`,
  `jsd_matched_post`, `kl_global_corpus`, `jsd_global_corpus`.
- `_stats_from_complete_pairs()` / `_clustered_paired_stats()` (lines 176–268):
  paired sign test + bootstrap CI, row-level and **clustered by `post_id`** (the
  honest inference unit).
- `sus_detection_*`: LLM judge picks the synthetic comment out of 10; summary has
  per-method detection rate + McNemar paired stats + a clustered summary.

`src/services/stego_metrics_service.py` computes the underlying numbers:
- `run_perplexity_metrics()` (GPT-2, lines 242–312).
- `run_divergence_metrics()` with `kl_divergence` / `js_divergence` over word-unigram
  counters vs `primary_baseline_matched_post` and `secondary_baseline_global_corpus`
  (lines 315–575).

The viewer renders these in `stego-results-viewer/src/app/zlg-comparison/` (winner
logic now direction-aware after the recent fix).

## 3. New metrics

Group by what they actually measure. Each is defined so it can be added to
`build_zlg_method_comparison_dataset.py` (`_metric_block` / `_quality`) and surfaced
in the viewer.

### 3.1 Direct human-likeness (headline)

**M1. Pairwise A/B "which reads like a real person?"**
- For each pair, show the judge **both** comments (ours + ZLG) in the same thread
  context, randomized order, and ask which is more likely written by a real
  commenter. Record win/lose/tie + a short rationale.
- Report **win rate** with clustered-by-post bootstrap CI. This is the most direct
  test of the exact claim ("our comments feel more genuine") and reuses the existing
  judge harness (one prompt, same model provenance as sus-detection).
- Guard against position bias: randomize order per pair, and optionally run both
  orders and average.

**M2. Sus-detection (keep, promote to headline).** Already implemented and favors us.
Report clustered-by-post as the primary number, row-level as secondary. Make this and
M1 the top of the dashboard, not perplexity.

### 3.2 Decompose "naturalness" into relevance vs writing quality

The single biggest clarity win: the divergence/perplexity metrics conflate *topical
fit* with *writing quality*. Split them.

**M3. Thread relevance (LLM, 1–5):** "Does this reply address the thread?" ZLG will
score higher (it stays on-topic); we expect ours to improve after the tangent-DB
revamp. This **names our real weakness (drift)** so we can track the revamp's effect.

**M4. Writing quality / coherence (LLM, 1–5 rubric):** grammaticality, coherence,
absence of run-ons/repetition, single-voice. We expect ours higher. Reporting M3 and
M4 separately is more honest than one muddled "naturalness" proxy.

**M5. Objective lexical-quality index (no judge, deterministic):** aggregate the
signals already in `_quality()` into one comparable index — e.g. combine
`unique_token_ratio` (↑), `repetition_ratio` (↓), `max_bigram_repeat` (↓),
run-on/length sanity. Cheap, reproducible, judge-independent corroboration of M4.
Report per-method means with clustered CIs.

### 3.3 Reframe the existing distributional metrics honestly

**M6. Relabel perplexity/KL/JSD as "topical-fit / fluency proxies," not
"naturalness."** No new computation; a documentation + viewer-labeling change:
- Add a one-line caveat on each card: "measures word-distribution overlap with the
  thread, not human authorship; low perplexity often flags AI text."
- Keep them (they are legitimate and expected — the paper should report the trade),
  but demote from headline. This is the same spirit as the existing capacity
  "CORRECTION" note.

### 3.4 Characterize the tangent DB itself (ties to [[tangent-db-revamp]])

**M7. Tangent-DB quality metrics** (from `tangent_db_report`):
- **DB relevance**: mean/median relevance score of kept tangents vs the thread.
- **DB distinctness**: mean pairwise Jaccard (or embedding distance) among kept
  tangents — high = diverse options, low = wasted capacity.
- **Source mix**: share of tangents from post/comments/search — a high search share
  predicts drift.
- **Capacity vs quality curve**: kept-count (→ bits) as thresholds vary. Lets us pick
  operating points and show the trade explicitly.
- Report **legacy vs v1** side by side (before/after the revamp).

**M8. Drift attribution:** cross the sus-detection "why it got caught" reasons with
angle source. Hypothesis: our detected cases disproportionately trace to
high-search-share / low-relevance tangents. Confirming this validates that the
tangent-DB revamp targets the actual failure.

### 3.5 Fairness / validity fixes (prerequisite, not optional)

**M9. Diversity guard:** assert each post contributes **distinct** our-method comments
across payloads (fail the build if uniqueness < threshold). Regenerate the comparison
so we are not pairing 47 comments against 304.

**M10. Cluster-level everything:** make clustered-by-post the default reported unit
for every metric (M1–M5), with the row-level numbers as secondary. The dataset
already has `_clustered_paired_stats`; extend it to the new metrics.

## 4. Implementation surface

- `scripts/build_zlg_method_comparison_dataset.py`:
  - extend `_quality()` and `_metric_block()` with M5/M7 fields;
  - add judge-driven M1/M3/M4 (new pass, mirroring `sus_detection`), writing
    `preference_results.jsonl`, `relevance_results.jsonl`, `quality_results.jsonl`
    and summaries;
  - extend `_stats_from_complete_pairs` / `_clustered_paired_stats` to include the new
    numeric metrics (M10).
- `src/services/stego_metrics_service.py`: reuse for M5; the LLM-judge metrics use the
  workflow LLM adapter (`resolve_workflow_llm_provider_and_model` +
  `LLMAdapter`) per CLAUDE.md — **do not hand-roll an OpenAI client**.
- Viewer `stego-results-viewer/src/app/zlg-comparison/`:
  - new top section: **M1 preference win-rate** + **M2 sus-detection** as headline;
  - a **relevance (M3) vs writing-quality (M4/M5)** two-axis panel;
  - caveat labels on the perplexity/KL/JSD cards (M6);
  - a **tangent-DB quality** panel (M7) with legacy-vs-v1 toggle.

## 5. Judge-metric rigor (so results are defensible)

- **Provenance:** pin judge model + version; record it in every summary (the
  sus-detection summary already stores `judge_model`). Reproducibility requires the
  judge prompt, decoy construction, and model provenance — carry all three.
- **Prompt-as-data:** the judge prompts for M1/M3/M4 are *evaluation* prompts, not
  workflow/system generation prompts, so they are **not** under the prompt red line.
  Keep them versioned and hashed in the summary regardless.
- **Bias controls:** randomize A/B order (M1); calibrate against the 10%-chance
  baseline (M2); report inter-run variance by repeating the judge pass with a fixed
  seed where the backend allows.
- **Multiple-comparison honesty:** we are adding several metrics; pre-register which
  are headline (M1, M2) vs exploratory (the rest) so we do not fish for a win.

## 6. Phased rollout

- **Phase 0:** fix diversity (M9) + make everything cluster-level (M10) on the
  *existing* method; re-report. This alone corrects the "lost on all fronts" framing.
- **Phase 1:** add M1 (preference) + M5 (objective quality) — cheap, high-signal,
  and both expected to favor us. Update viewer headline.
- **Phase 2:** add M3/M4 decomposition + M6 relabeling. Now the dashboard tells the
  true two-axis story (relevance vs quality).
- **Phase 3:** add M7/M8 tangent-DB metrics and run **legacy vs v1** to quantify the
  revamp's effect on drift and detection.

## 7. Success criteria

- The dashboard's headline is a **reader-facing** metric (preference + sus-detection),
  not perplexity.
- "Naturalness" is reported as two separable axes (relevance, writing quality), each
  with clustered CIs, instead of one conflated proxy.
- After the tangent-DB revamp, **thread relevance (M3) rises** and **sus-detection
  drops** for our method, with the tangent-DB metrics (M7) showing lower search-share
  / higher relevance — a causal chain from DB change → drift reduction → fewer
  detections.
- Every number is reproducible: dataset root ([[prepared-posts-separate-persistence]]),
  tangent-DB config hash ([[tangent-db-revamp]]), and judge provenance all recorded.

## 8. Risks / decisions

- **Judge cost/latency:** M1/M3/M4 add LLM passes over ~hundreds of pairs; batch and
  cache by (pair_id, metric, judge_version).
- **Judge as both generator and evaluator:** avoid using the same model family for
  generation and judging where it could bias; document the choice.
- **Don't over-claim:** ZLG legitimately wins topical-fit; the honest story is a
  *trade* (capacity + human-likeness vs topical mimicry), and the revamp aims to
  narrow ZLG's relevance edge without losing our writing-quality/detection edge.
