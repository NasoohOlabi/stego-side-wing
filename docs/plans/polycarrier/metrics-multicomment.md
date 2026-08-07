# POLYCARRIER — Metrics against multi-comment embedding

This document explains how every metric already used by the **zlg-comparison** batch
pipeline is implemented and calculated when our method embeds one payload across
**multiple stego comments (frames)**.

Two reporting layers are required. Mixing them silently produces wrong claims.

| Layer | Unit | What it answers |
|---|---|---|
| **Frame / comment** | One ours stego comment ↔ one ZLG stegotext | Fair per-carrier quality (what `/zlg-comparison` shows today) |
| **Sample / payload** | One multi-frame encode (`sample_index`) spanning N frames | Does the whole large payload recover? What is end-to-end efficiency? |

The stock scripts score the **frame** layer. POLYCARRIER sample-level rollups are
defined here and must be applied when quoting multi-comment results.

---

## 0. Multi-comment data model

### 0.1 Ours encoding

```text
stream = EliasGamma(frame_count) || EliasGamma(payload_bit_length) || payload_bits || zero_pad(last_frame)
frames = plan_payload_frames(payload, posts, max_frames_per_post)
# each frame: pick parent + tangent on one post; emit one visible stego comment
```

Per-frame recoverable capacity (lossless):

```text
frame_bits[i] =
  floor(log2(comment_choices[i])) + floor(log2(tangent_choices[i]))
  # only when the corresponding choice count > 1
```

Sample (payload) capacity:

```text
sum_frame_bits     = Σ_i frame_bits[i]
control_bits       = recovery_meta.control_bit_length   # Elias-gamma headers
useful_payload_bits = payload_utf8_bytes * 8            # e.g. 256 for --payload-bytes 32
total_stream_bits  = control_bits + useful_payload_bits (+ pad in last frame only)
# require: sum_frame_bits >= total_stream_bits
```

Artifacts from `run_multi_frame_batch_e2e.py`:

- `summary.records[s]` — sample `s`: `frame_count`, `decode.payload_match`, `recovery_meta`, `entries`
- `summary.entries[]` — flat list of frames; each has `sample_index`, `frame_index`,
  `post_id`, `embedded_bits`, `output_file`, `payload_hash`

### 0.2 How ZLG pairs today (frame layer)

`run_zlg_batch_comparison.py` iterates `summary.entries[]` (one ZLG hide per ours
**comment**). For `capacity_matched`:

```text
our_embedded_bits = output_file.embedding.commentEmbedding.bitsCount   # = frame_bits[i]
fair_payload_bytes = max(1, ceil(our_embedded_bits / 8))
zlg_secret = utf8_prefix(full_sample_payload, fair_payload_bytes)
```

So ZLG is matched to **that frame’s bit budget**, not to the full 256-bit sample secret.
That is intentional for equal-payload fluency per carrier. Full-payload correctness is a
**sample-layer** gate (`decode.payload_match` on ours; optional multi-carrier ZLG is a
separate experiment).

### 0.3 Joining keys

| Key | Use |
|---|---|
| `output_file` / `source_key` | Frame pair ours ↔ ZLG |
| `(post_id, sample_index)` | Cluster frames of one sample on one post |
| `sample_index` | Whole multi-comment payload (may span multiple `post_id`s) |
| `payload_hash` | Stable identity of the useful secret |

Independent statistical unit for inference remains **`post_id`** (cluster before
bootstrap / sign test), same as the existing comparison summary.

---

## 1. Capacity and efficiency

### 1.1 Frame layer (existing builder)

**Ours** — `selection_channel_capacity_report` on the post snapshot **with `angles`**:

```text
selection_bits = recoverable_capacity_bits
               = floor(log2(comment_choices)) + floor(log2(tangent_choices))
payload_bits_encoded = selection_bits   # batch lane
protocol_overhead_bits = 0              # batch lane accounting
total_embedded_bits = selection_bits
```

If `--dataset-dir` lacks `angles`, tangent width collapses to 0 and capacity is
under-reported (see `docs/reports/zlg-sample-audit-2026-07-27.md`). POLYCARRIER always
points `--dataset-dir` at the angles dir.

**ZLG** — 16-bit framing header:

```text
payload_bits_encoded   = useful recovered bits (exclude header)
protocol_overhead_bits = 16
total_embedded_bits    = payload_bits_encoded + protocol_overhead_bits
```

**Bits per word (viewer, frame-level):**

```text
bpw_frame = total_embedded_bits / word_count
```

**Reliability (ZLG attempts):**

```text
denom = attempts where failure_stage != harness_extract
effective_payload_bits_per_attempt = sum(accepted useful bits) / denom
# failures contribute 0 bits
```

### 1.2 Sample layer (POLYCARRIER — implement when reporting)

For each `sample_index` with ours decode success:

```text
# Ours
useful_bits_sample     = len(payload.encode('utf-8')) * 8
control_bits_sample    = records[s].recovery_meta.control_bit_length
sum_frame_bits_ours    = Σ entries[e].embedded_bits  for e.sample_index == s
sum_words_ours         = Σ word_count(frame_stego)    for frames of s
bpw_sample_ours        = useful_bits_sample / sum_words_ours
# optional gross efficiency including control:
bpw_gross_ours         = (useful_bits_sample + control_bits_sample) / sum_words_ours

# ZLG (capacity_matched): sum accepted paired frames for the same sample_index
useful_bits_zlg_sample = Σ zlg.payload_bits_encoded for accepted frames of s
sum_words_zlg          = Σ word_count(zlg.stegotext) for those frames
bpw_sample_zlg         = useful_bits_zlg_sample / sum_words_zlg
```

**Never** compare `useful_bits_sample` (256) to a single ZLG frame’s bits. Compare:

1. Frame↔frame under `capacity_matched` for fluency, or
2. Sample↔sample sums above for end-to-end efficiency.

Utilization (ours, sample):

```text
utilization_percent =
  100 * (useful_bits_sample + control_bits_sample) / sum_frame_bits_ours
```

---

## 2. GPT-2 perplexity

### 2.1 Frame layer (existing)

`stego_metrics_service.compute_text_perplexity` on each stego string:

```text
PPL = exp( Σ NLL / Σ scored_tokens )
```

Causal LM (`gpt2`), sliding windows (stride default 512), each token scored once.
Field: `perplexity_gpt2`. Lower = more predictable under GPT-2 (not human-likeness).

Built by `build_zlg_method_comparison_dataset.py` → `run_single_post_metrics`.

Standalone (optional):

```powershell
uv run python scripts/avg_perplexity.py `
  --output-dir metrics/e2e_runs/polycarrier_256b_smoke/output-results `
  --metrics-dir metrics/e2e_runs/polycarrier_256b_smoke/metrics `
  --model-name gpt2 --device auto
```

### 2.2 Sample layer (POLYCARRIER)

Token-weighted pool across frames of one sample (preferred over mean of PPLs):

```text
PPL_sample = exp( Σ_frames (log(PPL_f) * tokens_f) / Σ_frames tokens_f )
# equivalently recompute NLL over concatenated frame texts only if you accept
# cross-frame boundary artifacts — prefer per-frame NLL pooling:
PPL_sample = exp( Σ_f NLL_f / Σ_f tokens_f )
```

Report **median** and token-weighted mean across samples; do not lead with a raw mean of
frame PPLs when frame lengths differ.

---

## 3. KL / JSD (word unigrams)

### 3.1 Frame layer (existing)

Tokenization: `TOKEN_RE = [A-Za-z0-9']+` (lowercased). Smoothing α = `1e-6`:

```text
P(t) = (count(t) + α) / (N + α|V|)
KL(stego ‖ baseline) = Σ_t P_s(t) log(P_s(t) / P_b(t))
JSD = ½ KL(P‖M) + ½ KL(Q‖M),  M = ½(P+Q)
```

| Field | Baseline |
|---|---|
| `kl_matched_post` / `jsd_matched_post` | All non-empty comment bodies on that `post_id` (incl. nested) |
| `kl_global_corpus` / `jsd_global_corpus` | All comments in `--dataset-dir` |

Direction is always **KL(stego ‖ baseline)**.

Standalone:

```powershell
uv run python scripts/avg_kld.py `
  --output-dir metrics/e2e_runs/polycarrier_256b_smoke/output-results `
  --dataset-dir <angles_dir> `
  --metrics-dir metrics/e2e_runs/polycarrier_256b_smoke/metrics `
  --alpha 1e-6
```

(`avg_er.py` is **not** in this chain — character-length only.)

### 3.2 Sample layer (POLYCARRIER)

Concatenate all frame stego texts for sample `s` (space-separated), then run the same
unigram KL/JSD against:

- **Matched multi-post baseline:** union of comment bodies for every `post_id` used by
  sample `s` (a multi-comment payload may span several posts)
- **Global corpus:** unchanged (`--dataset-dir`)

Also report the **mean of per-frame KL** as a descriptive diagnostic; primary claim uses
the concatenated distribution so longer frames dominate correctly.

Length and sparsity confound KL/JSD — always show `sum_words` beside them.

Recompute helper:

```powershell
uv run python scripts/recompute_paired_divergence_metrics.py `
  --zlg-run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --dataset-dir <angles_dir> --alpha 1e-6
```

---

## 4. Lexical / handcrafted + naturalness gate

### 4.1 Frame layer (existing)

| Field | Formula |
|---|---|
| `word_count` | `#` of `TOKEN_RE` matches |
| `unique_token_ratio` | unique / tokens |
| `repetition_ratio` | `1 − unique_token_ratio` |
| `lexical_diversity_mattr` | MATTR, window 10 |
| `lexical_quality_index` | `100 * (0.55·MATTR + 0.30·bigram_score + 0.15·length_sanity)` |

Shared gate: `evaluate_naturalness(text)` → `quality_passed` (both methods).  
ZLG server verdict: `server_quality_passed` (keep separate).

### 4.2 Sample layer (POLYCARRIER)

```text
word_count_sample     = Σ_f word_count_f
# micro-average uniqueness over the concatenated token multiset:
unique_token_ratio_s  = |set(all tokens)| / |all tokens|
repetition_ratio_s    = 1 - unique_token_ratio_s
# gate: sample passes only if EVERY frame passes (strict), or report
#   frames_passed / frames_total (rate) — publish both; primary = rate
gate_pass_rate_sample = mean(quality_passed_f)
gate_all_pass_sample  = all(quality_passed_f)
```

MATTR / LQI on the concatenated text is allowed as a secondary descriptor; primary
comparison stays frame-paired (equal text length regime closer to ZLG’s one-carrier text).

---

## 5. Reference metrics (BLEU / ROUGE / BERTScore / self-consistency)

### 5.1 Frame layer (existing)

Reference = **first human comment body** in that post’s dataset JSON.

| Metric | Implementation |
|---|---|
| BLEU | `sacrebleu.sentence_bleu(candidate, [reference])` |
| ROUGE-1/2/L | `rouge_score` F-measure, stemmed |
| BERTScore P/R/F1 | `roberta-large`, `rescale_with_baseline=True` |
| Self-consistency | Mean cosine sim vs other same-`post_id` same-method texts (`all-MiniLM-L6-v2`) |

Open-ended Reddit replies have many valid answers — proxies only.

```powershell
uv run python scripts/recompute_paired_bertscore.py `
  --input <run>/comparison_dataset/paired_rows.jsonl `
  --device cpu --bertscore-model roberta-large
```

### 5.2 Sample layer (POLYCARRIER)

```text
# Macro-average frame scores within the sample (equal weight per carrier)
BLEU_sample = mean_f(BLEU_f)   # same for ROUGE-L, BERTScore-F1
```

For self-consistency under multi-comment: average pairwise cosine similarity among
**frames of the same sample and method** (intra-sample), and separately among frames that
share `post_id` (existing definition). Label which definition you publish.

Skip reference metrics only with `--skip-reference-metrics` for debug builds.

---

## 6. Acceptance, decode, failure stages

### 6.1 Frame layer (existing — ZLG + paired rows)

From `results.jsonl`:

- `accepted`, `decode_ok`, `failure_stage`
- Intention-to-treat: keep failures in reliability denominators
- Quality metrics conditional on acceptance must be labeled as such

### 6.2 Sample layer (POLYCARRIER — ours primary gate)

```text
sample_encode_ok  = records[s].succeeded
sample_decode_ok  = records[s].decode.payload_match == true
sample_frame_count = records[s].frame_count

# ZLG sample acceptance (capacity_matched):
zlg_frames_accepted_s = count accepted ZLG rows with sample_index == s
zlg_sample_all_frames_ok = (zlg_frames_accepted_s == ours_frame_count_s)
zlg_sample_frame_accept_rate = zlg_frames_accepted_s / ours_frame_count_s
```

Primary multi-comment claim:

```text
ours_end_to_end_success_rate =
  (# samples with encode_ok ∧ decode_ok) / (# samples attempted)
```

Do not call a sample “recovered” because one of its frames matched ZLG bits.

---

## 7. Paired statistics (existing summary.json)

Built by `build_zlg_method_comparison_dataset.py` at the **frame-pair** grain:

```text
cluster by post_id
per metric: post-cluster mean(ours), mean(zlg)
Δ = mean_zlg - mean_ours   # sign depends on lower-is-better
sign test + Holm within metric family
```

Also emitted: `methods`, `methods_clustered_by_post`, `zlg_attempt_reliability`,
`shared_naturalness_gate`, `diversity_guard`, `independence_diagnostics`,
`paired_statistics`, `row_level_descriptive_statistics`.

### POLYCARRIER sample-clustered companion

When quoting sample-level metrics, build an analogous table via
`services.polycarrier_sample_metrics.sample_paired_statistics` (also emitted on
`sample_layer_metrics.json` as `paired_statistics`):

```text
default: one observational unit per sample_index
optional --cluster-by-primary-post: cluster by first post_id in records[s].post_ids
per metric: Δ = mean_zlg - mean_ours; two-sided sign test + Holm within family
```

Prefer restricting smoke/pilot analysis to **sample_index** as the descriptive unit and
`post_id` only for frames that share a post, until multi-post clustering is required.

---

## 8. Codex LLM judges (existing dashboard panel)

Five metrics: `standout`, `weak_link`, `suspicion`, `attribution`, `register`.

```powershell
uv run python scripts/run_codex_judge_campaign.py `
  --run-dir metrics/zlg_comparison_runs/zlg_polycarrier_256b_smoke `
  --phase auto --pilot-limit 50 --max-workers 4
```

Judges operate on **paired frame rows** (one stego comment in thread context). That remains
the primary detection/suspicion comparison.

**Sample-layer extension (optional):** judge the **set** of stego comments for one sample
as a thread contribution (multi-comment presence). Only do this with a frozen prompt file
and a new metric name (e.g. `suspicion_multicomment`) — do not overload frame-level
`suspicion` scores.

Artifacts: `comparison_dataset/codex_judgments/{metric}_judgments.jsonl`,
`codex_judge_summary.json`, `pilot_audit.json`.

---

## 9. `capacity_matched` vs `max_capacity`

| Mode | POLYCARRIER use |
|---|---|
| `capacity_matched` | **Default.** Per-frame bit budget matched to ours `bitsCount`. Fluency, lexical, reference, judges. |
| `max_capacity` | Separate run-dir only. Capacity story; never cite as equal-payload fluency. Note `capacity_censored` if ZLG hits probe ceiling (~256 bits/carrier). |

---

## 10. End-to-end implementation checklist

Score everything the dashboard already knows (frame layer), then add sample rollups:

1. Phase 1–3 in [`execution-plan.md`](execution-plan.md) → `paired_rows.jsonl` +
   `comparison_dataset/summary.json` (frame metrics: capacity, PPL, KL/JSD, lexical, gate,
   BLEU/ROUGE/BERTScore, self-consistency, acceptance).
2. From `summary.records` + `paired_rows`, compute sample-layer tables in §1.2–§6.2 via
   `uv run python scripts/aggregate_polycarrier_sample_metrics.py`
   (`services.polycarrier_sample_metrics`).
3. Optional Codex campaign (§8).
4. `audit_paired_sample_artifacts.py` for capacity provenance.
5. Point `ZLG_RUN_DIR` at the ZLG run; read frame panels from `/zlg-comparison`; cite
   sample numbers from this doc’s formulas.

### Metric → where it is calculated

| Metric | Frame implementation | Sample rollup |
|---|---|---|
| Useful / total bits, BPW | `build_zlg_method_comparison_dataset` + viewer BPW | §1.2 |
| GPT-2 PPL | `compute_text_perplexity` / `run_single_post_metrics` | §2.2 token-weighted |
| KL/JSD matched + global | `stego_metrics_service` unigram KL/JSD | §3.2 concat |
| Lexical / LQI / gate | builder + `evaluate_naturalness` | §4.2 sum / rates |
| BLEU / ROUGE / BERTScore | sacrebleu / rouge / bert-score | §5.2 macro mean |
| Self-consistency | MiniLM cosine | §5.2 intra-sample |
| Acceptance / decode | `results.jsonl` + ours `records[].decode` | §6.2 |
| Paired Holm / sign test | `comparison_dataset/summary.json` | §7 companion |
| Codex 5 judges | `run_codex_judge_campaign` | optional multicomment judge |
| Reliability / failure stage | ZLG summary | keep ITT; sample all-frames-ok |

---

## 11. What not to claim

- Do not treat per-frame `selection_bits` sum as “useful recovered payload” without
  subtracting Elias-gamma control and confirming `payload_match`.
- Do not average frame PPLs without token weights when lengths differ.
- Do not use `max_capacity` fluency as a matched comparison.
- Do not pair against stale `--dataset-dir` defaults.
- Do not cite unpaired historical LUCID/ZLG runs as POLYCARRIER results.
