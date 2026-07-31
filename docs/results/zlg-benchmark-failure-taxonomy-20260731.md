# ZLG benchmark: where the 45% failure rate actually came from

**Date:** 2026-07-31
**Run audited:** `metrics/zlg_comparison_runs/zlg_batch_scale300` (554 rows, 304 accepted, 250 failed)
**Reported before:** 554 trials, 54.9% acceptance, 8.8 effective payload bits/attempt
**Reported after:** 460 trials, 66.1% acceptance, 10.6 effective payload bits/attempt

## Summary

The headline failure rate was not a property of the ZLG baseline. It combined three
unrelated things: a bug in our own harness, a client that never retried, and a
server-side quality gate that rejects ordinary English. Only the third is about ZLG
at all, and even then it is a configuration artifact rather than an encoding limit.

## Failure taxonomy

| Stage | Count | % of 554 | Whose fault |
| --- | ---: | ---: | --- |
| `harness_extract` — "not enough cover sentences" | 94 | 17.0% | Ours. No HTTP request was ever sent. |
| `quality_gate` — HTTP 422 | 133 | 24.0% | Server gate, one attempt each |
| `leakage_check` — prompt leakage | 22 | 4.0% | Reasoning-model `<think>` blocks |
| `hide_request` — HTTP 500 | 1 | 0.2% | Server fault |

### 1. The 94 harness failures never reached the server

`_build_cover_texts` returned comment sentences whenever *any* existed, and only fell
back to title/selftext when there were **zero**. A post whose picked chain yielded
exactly one usable sentence returned a single-element list, tripped the `len < 2` guard
in `_extract_sample`, and was recorded as a ZLG failure.

They concentrate in **21 posts out of 154**, and every sample from those posts failed.
Re-running the fixed `_extract_sample` over all 94 source files recovers **94/94**
(3–19 cover sentences each, median 4).

Counting these against ZLG's acceptance rate measured our extraction code, not the
baseline. `_zlg_attempt_reliability` now reports them as `harness_skipped` and excludes
them from the denominator.

### 2. The 133 gate rejections each got exactly one attempt

Every one carries `attempt: 1` with server-side `retry: 4`. The committed
`run_comparison_sample` returned on the first hide exception and built the prompt once
outside the retry loop, so even a retry would have re-sent an identical request. A
quality-gate rejection is a property of the sampled text, so a fresh draw is a
genuinely new trial. Fixed by `is_retryable_hide_error` plus per-attempt prompt
reseeding; default `--max-retries` raised 3 → 5.

### 3. The gate rejects a quarter of human writing

The deployed gate is `repetition_metrics` / `quality_pass` in `src/zgls_api.py` of the
ZGLS service repo on the `asus` host. Its rules, confirmed by contrasting the 304
accepted against the 133 rejected:

| Rule | Accepted (n=304) | Rejected (n=133) |
| --- | --- | --- |
| any non-ASCII character | **0** | 28 |
| `max_bigram_repeat >= 2` | **0** | 76 |
| `repetition_ratio >= ~0.28` | max 0.273 | 25 |
| `word_count > 40` | max 38 | 19 |
| unbalanced double quote | **0** | 39 |

Two of these fire on ordinary English:

- **Non-ASCII.** Checking the actual codepoints across all 133 rejections finds only
  `’` (30), `…` (8), `”` (2), `“` (1). **There is no mojibake.** Comments were rejected
  for containing a curly apostrophe.
- **`max_bigram_repeat >= 2`.** Any comment repeating one word-bigram fails. "of the"
  twice in a 25-word sentence is normal English. This alone accounts for 76/133 (57%).

Applying just these rules to the run's own 914 human cover sentences rejects **23.3% of
real human Reddit comments**.

The remaining rejections are genuine generation defects with upstream causes:
`<think>` blocks from a reasoning model (9), markdown/bracket artifacts (30), and
dangling opening quotes where the model starts with `"` and `complete_sent=True`
truncates before the closing one (41 — note **111 of the 304 accepted texts also open
with a quote**, they simply closed it).

## What changed

**Harness (`stego-side-wing`)**

- Cover sentences back-fill from post title/selftext instead of stranding the sample.
- Quality-gate rejections and transient faults retry with a freshly seeded prompt;
  contract errors still fail immediately.
- Every result carries a typed `failure_stage`, so nothing downstream has to regex a
  status code out of a JSON blob embedded in a message string.
- `build_api_prompt` no longer frames examples as `- ` bullets or ends with a
  `Comment:` label, both of which primed the quoted, markdown-wrapped output above.
- `summary.json` distinguishes `entries_this_invocation` from `rows_total` (the old file
  claimed `total_entries: 53` beside 554 rows) and records the server's `/health`
  identity.

**Evaluation (`services/naturalness_gate_service.py`, new)**

One gate definition applied to **both** methods. Our method's `quality_passed` was
previously hardcoded `True` in the dataset builder while ZLG carried the server's
verdict, so the two were never comparable.

Recalibration: NFKC-fold typography rather than reject it; keep genuine `U+FFFD` and
control characters fatal; bigram limit 2 → 4; word cap 40 → 60; keep the
degenerate-repetition and unbalanced-double-quote rules. Single-quote parity is
deliberately *not* checked — it flagged 261 of 908 human comments, once per contraction.

Human rejection: **23.3% → 5.0%**. `scripts/calibrate_naturalness_gate.py` exits
non-zero above a 5% budget.

**Two results worth stating plainly:**

- The new gate still rejects **71%** of the previously rejected 133. Those texts
  genuinely contain `<think>` blocks, unbalanced quotes and degenerate repetition. The
  fix for them is upstream — prompt and decode configuration — not a looser gate.
- It rejects **22 of the 304 previously *accepted*** samples. These are meta-commentary
  leakage like `[The user wants you to generate a short comment...]` that the deployed
  gate missed. Under the shared gate: our method 304/304 (100%), ZLG 282/304 (92.8%).

## Corrected figures

| Metric | Before | After |
| --- | ---: | ---: |
| Trials in denominator | 554 | 460 |
| Acceptance rate | 54.9% | 66.1% |
| Effective payload bits/attempt | 8.78 | 10.57 |

These come from re-analysing the **same** run with corrected accounting — no
re-generation. A re-run against the recalibrated server is required before these are
final, since the prompt change and the gate recalibration both affect generation.

## Still open

- Re-run the benchmark against the redeployed server and regenerate the dataset.
- Update the figures cited in `project_paper`.
## Decided against: switching the client to the advisory gate

The plan called for the client to send the server's opt-out flag and let our shared gate
be the only judge. Reading the server disproved it, twice over.

First, the flag already exists — `HideRequest.enforce_quality`, not a new
`strict_quality`. Nothing needed adding.

Second, it does not actually report the verdict. `quality_passed` is assigned the
*overall acceptance* decision, not `quality_ok`, so with `enforce_quality=false` a
gate-failing generation returns 200 with `quality_passed=true`, and `quality_metrics`
carries raw fields but no boolean. A client would have to re-implement `quality_pass` to
recover the verdict — and a benchmark run in that mode would record every sample as
gate-passing. (Relatedly, the `if decode_ready and not enforce_quality` branch is
unreachable: `is_truncated` and `decode_ready` are mutually exclusive, so truncation
raises regardless of the flag.)

Third, and decisive: the gate sits *inside* the server's retry loop, at

```python
passed = (not is_truncated) and decode_ready and (quality_ok or not enforce_quality)
```

so `enforce_quality=false` makes the loop accept its first draw and ZLG silently loses
all four of its quality retries. That would **disadvantage the baseline** — the opposite
of what this whole exercise is for.

The right framing is two gates doing two different jobs. The server gate is ZLG's
*generation-time* quality control, the counterpart of our own pipeline's retries; the
shared `naturalness_gate_service` is the *analysis-time* yardstick both methods are
measured against. Keeping the server gate on and recalibrated is what makes the
comparison fair, so the client stays fail-closed.

## Server-side changes (ZGLS service repo on `asus`)

Four commits on that repo's `main`, none pushed:

| SHA | What |
| --- | --- |
| `ccff266` | Threshold recalibration: non-ASCII → `replacement_char_count`, bigram ≤1 → ≤3, `max_words` 40 → 60 |
| `5c4bcaf` | Matching test updates |
| `5246767` | `/health` reports `git_commit`, `git_dirty` and the full active threshold set |
| `11cab87` | Human-corpus calibration test |

Calibration on the server's own 50-comment corpus: **72% → 0% rejected**. Suite is 46
passed, 6 skipped (the skips need a live `ZGLS_SERVER_URL`).

## Still open

- Re-run the benchmark against the redeployed server and regenerate the dataset.
- Update the figures cited in `project_paper`.
- `quality_passed` should carry `quality_ok` rather than the overall acceptance decision,
  with acceptance moved to its own field. Harmless today because we run fail-closed, but
  it silently mislabels every sample for anyone who uses `enforce_quality=false`.
- The server's own `quality_max_repetition_ratio` is still 0.65 while this repo's mirror
  uses 0.28. With the bigram limit relaxed, 0.65 no longer catches phrase-level
  degeneracy — it passes "...the most famous person on earth is also the most famous
  person on earth" (`repetition_ratio` 0.444, `max_bigram_repeat` only 2). The analysis
  gate catches it, so no result is wrong, but the server will now waste retries emitting
  text the yardstick will reject.
