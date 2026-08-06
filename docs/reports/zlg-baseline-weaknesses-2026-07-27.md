# Measured weaknesses of the ZLG baseline — 2026-07-27

Every claim here is computed from artifacts produced by our own runs of the official
Zero-shot Generative Linguistic Steganography (ZLG/ZGLS) service. Nothing in the main body is
sourced from reading the upstream implementation; unexecuted code-level observations are
quarantined in the [appendix](#appendix-unverified-code-reading-only) and must not be cited
until reproduced.

Sources: `metrics/zlg_comparison_runs/zlg_demo_20260712_v6_200/results.jsonl` (547 attempts),
`.../comparison_dataset/paired_rows.jsonl` (304 accepted ZLG rows over 47 posts),
`metrics/zlg_comparison_runs/zlg_capacity_sweep_20260712/results.jsonl`. Regenerate with the
command in [`zlg-sample-audit-2026-07-27.md`](zlg-sample-audit-2026-07-27.md#reproduce).

Service parameters for the run (`results.jsonl.params_used`): `mode=huffman`,
`threshold=0.01`, `temperature=0.7`, `temperature_alpha=1.0`, `max_bpw=2`,
`max_new_tokens=256`, `quality_min_words=8`, `quality_max_words=75`, `quality_max_retries=4`.

## 1. Reliability: 44.4% of attempts produce nothing

| Outcome | Attempts | Share |
| --- | ---: | ---: |
| Accepted (hide + verified reveal, byte-exact) | 304 | 55.6% |
| Rejected by the relaxed quality gate | 166 | 30.3% |
| `exception: not enough cover sentences extracted for ZLG prompt` | 75 | 13.7% |
| `hide_failed` (HTTP 422 from the service) | 2 | 0.4% |

Two distinct failure modes, both structural:

- **Quality collapse.** The 166 gate rejections are dominated by repetition — recorded
  `repetition_ratio` up to 0.51 at 47-95 words.
- **Cover starvation.** ZLG needs `n_cover` extractable cover sentences to build its prompt. On
  75 attempts the thread could not supply them and no carrier existed at all. Our selection
  channel has no analogous precondition: it needs a comment list, which every thread has.

Conditional capacity of 76.70 bits/comment therefore corresponds to **41.77 bits per attempted
carrier** once failures are counted, the figure that belongs beside any throughput claim.

## 2. Capacity does not scale — it only lengthens the text, then breaks

`zlg_capacity_sweep_20260712`, same service and parameters:

| Payload | Status | bits/word | Decode verified |
| ---: | --- | ---: | --- |
| 5 B | 200 | 1.21 | yes |
| 10 B | 200 | 1.78 | yes |
| 20 B | 200 | 1.76 | yes |
| 30 B | 200 | 1.82 | yes |
| 49 B | **422** | — | — |

Bits per word plateaus at ~1.8, so payload is bought only with length. The 49-byte attempt
(392 bits) failed the quality gate after 4 retries with `last_fail_reason: truncated`, and its
best candidate was **217 words with a repetition ratio of 0.49** — a Reddit comment that is
anomalous on length alone before any steganalysis. The practical per-carrier ceiling in this
configuration is ~30 bytes.

## 3. Detectability: surface artifacts on the accepted outputs

Rates over the 304 accepted ZLG rows, with our method on the same 47 posts for scale:

| Feature | ZLG | ours |
| --- | ---: | ---: |
| Begins with a leading space | **100.0%** | 0.0% |
| Begins with a quotation mark | 45.1% | 0.0% |
| Unbalanced double quotes | 6.6% | 0.0% |
| >40 words in a single sentence (run-on) | **71.1%** | 3.9% |
| Single-sentence output | 81.3% | 54.9% |
| Verbatim repeated 5-gram | 8.2% | 0.0% |
| Contains an Arabic numeral | **1.0%** | 16.8% |
| Space before punctuation | 0.7% | 0.0% |

Single-feature rank-AUC against 716 human comments from the same threads: leading space
**1.000**, words per sentence **0.941**, type-token ratio 0.816, word count 0.774.

Two caveats to state honestly when publishing this:

- Leading space and quote-wrapping are **normalisation-removable** — any deployment could strip
  them. They are not a durable security property of the baseline and should be reported as
  harness/serialisation artifacts.
- The durable discriminators are the ones a normaliser cannot fix: run-on sentence structure,
  verbatim repetition, and numeral avoidance.

**Numeral avoidance is the most interesting of these.** Candidate pruning at `p ≥ τ` steers the
model away from digit tokens, so quantities come out spelled and mangled:

> *"The Environmental Protection Agency (EP A ) is planning on eliminating its scientific
> research office by the year of two thousand and thirty-four (two zero three four ) ."*

A human comment about a news thread cites numbers 16.8% of the time in this corpus; ZLG does so
1.0% of the time. That is a topic-independent statistic a passive warden can compute.

## 4. The structural weakness: ZLG is not conditioned on the thread

ZLG builds its prompt from `n_cover` sampled cover sentences, not from the conversation the
carrier is posted into. In this run it answers threads it was never shown. On post `1lpx80x`
(*"Sean 'Diddy' Combs found not guilty of racketeering and sex trafficking…"*) the accepted ZLG
carrier reads:

> *"The prosecution's argument that Trump was angry enough to order his removal seems
> far-fetched given that Trump's own words in the recording show he was actually trying to calm
> the mob and prevent a coup attempt…"*

Fluent, low-perplexity (GPT-2 PPL 31), byte-exact decodable — and not a reply to anything in
the thread. The same run gives ZLG the better G-Eval relevance score (2.368 vs 1.924), which
says more about a judge scoring isolated text than about carrier plausibility: a reply that
answers a different news story is trivially flagged in situ, where it would actually be posted.

This is the argument the paper should lead with. GPT-2 perplexity and unigram KL/JSD reward
generic fluent English and cannot see thread-fit, so they systematically over-credit an
unconditioned generator. The comparison needs a reply-appropriateness / thread-conditioning
metric — evaluated with the parent comment in context, ideally against human replies from the
same thread — as a first-class endpoint alongside capacity.

Supporting evidence already in the run: thread-grounded factuality favours our method
(1.783 vs 1.564), and the clustered synthetic-detection rate favours it too
(23.9% vs 41.2%, mean delta 0.173, bootstrap 95% CI [0.064, 0.282], sign-test p=0.0095).

## 5. What ZLG legitimately wins

Stated plainly so the dossier is not one-sided: conditional on a successful hide, ZLG carries
76.70 bits/comment against our corrected 18.66, produces an independent output per trial
(self-consistency 0.295 vs our 1.000), and scores better on GPT-2 perplexity (row means 36.1 vs
104.5) and on unigram KL/JSD. Our texts are also shorter (33.9 vs 51.0 words), which inflates
sparse-unigram divergences against pooled distributions — a confound in ZLG's favour that
length-matched controls in the next benchmark must remove.

## Appendix: unverified, code-reading only

Recorded for future work. **Not for publication** until each is reproduced against
`tmp_zero_shot_gls_official` or the GPU service, with an artifact.

- Extraction assumes lossless retokenisation of `prompt + stegotext`; the working service side-
  steps this by transmitting `stego_token_ids`, which a real channel would not carry.
- The candidate set is a knife-edge at `p ≥ τ`; Huffman ties over a floored vocabulary appear to
  be resolved by container iteration order, implying sender and receiver need the same compiled
  binary.
- Shipped CLI defaults appear to disagree between hide (`--egs-mode huffman`, alpha 1.25) and
  extract (`--egs-mode block`, alpha 1.0).
- `bit2plain.py` appears to pass `ef_rounds=` into a function whose parameter is `ef_bits`,
  making the flag a no-op.
- Paper-vs-code deltas: shipped prompt template vs the one printed in the paper; the annealing
  update direction vs Eq. 4; script default τ vs the paper's 0.005; `evaluate/bpw.py` counting
  framing bits as payload; `evaluate/ppl.py` reporting the entropy of the modified selection
  distribution rather than an LM perplexity of the stegotext.

Reproducing the last group would let the related-work section explain why upstream's published
BPW and PPL are not directly comparable to ours. Until then, compare only against numbers we
generated ourselves.
