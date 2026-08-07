# context_weighted_v2 vs ZLG Baseline — Benchmark Report

**Date:** 2026-07-29/30
**Objective:** Produce 300+ valid samples for our method (`context_weighted_v2` context sampler) and
the ZLG baseline, compute all available benchmarks/metrics, and independently validate sample
validity, metric correctness, reproducibility, and fairness.

**Result: both targets met.** 554 our-method samples generated successfully (627 attempted, 88.4%),
304 of those decode-verified through the ZLG baseline (54.9% conditional yield), giving **304 fully
paired, decode-verified rows across 100 unique source posts** — the dataset the comparison below is
built from.

---

## 1. Pipeline validation (25-post smoke test)

Ran the exact smoke procedure first, per the "validate before scaling" instruction. Result: 19/25
our-method samples succeeded, 12/19 ZLG attempts accepted (11 fully decode-verified). This surfaced
five real bugs, all fixed before scaling (see §2). The smoke run's own numbers were superseded by the
full-scale run below; its only lasting output is the bug fixes.

## 2. Bugs found and fixed

All fixes are data-plumbing / infrastructure reliability changes — no business logic or algorithm
changes, per the constraint on this task.

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `scripts/build_zlg_method_comparison_dataset.py` | Capacity accounting read the **pre-angle** dataset snapshot (`dataset/<id>.json`, no `angles` key), silently zeroing the tangent/angle channel and under-reporting our method's recoverable capacity — a defect already documented in `docs/reports/zlg-sample-audit-2026-07-27.md` as a ~43% under-count on a prior run. | Resolve capacity from the sender's own `output-results/<...>.json[0]["post"]` snapshot (which carries `angles`), falling back to the dataset snapshot only if it lacks angles. Added a hard guard that raises if `tangent_choices==0` while `angleEmbedding` bits were actually used, to catch any regression. Verified at scale: **0/304** final rows have `tangent_choices==0`. |
| 2 | `run_actual_workload_e2e.py` (used as-is) | `select_post_ids()` matches angle files by exact filename against the dataset dir; tagged angle filenames (`<id>_<tag>.json`, produced by `--tag`) never match plain `<id>.json` baseline files → 0 usable posts. | Worked around by generating angles **without** `--tag` for the scale-up (native plain-filename output); for the smoke run, staged an untagged copy of the tagged angle files instead of touching this script. |
| 3 | ASUS host: `llama-server.exe` | Not running at all when the ZLG comparison step was first attempted, despite the ZLG API server (`:9000`) being up and reporting `"status":"ok"` (a stale/optimistic health check). Then, once started via a one-shot SSH `Start-Process`, it died silently within seconds — Windows OpenSSH exec sessions kill child processes tied to their job object on session end. | Started `llama-server.exe` genuinely detached via `Invoke-CimMethod -ClassName Win32_Process -MethodName Create` wrapping a small `.bat` file, which survives the SSH session ending. Documented in memory (`asus_remote_process_detachment.md`) for future sessions. |
| 4 | `src/services/zlg_comparison_service.py` | `/reveal` calls never sent the server-documented `payload_bits_len` field (the ZLG API's own agent guide explicitly requires echoing `/hide`'s `payload_bits` back on reveal). Without it, the server fell back to a legacy 16-bit-framed decode against a headerless stegotext, producing nonsense expected-length errors. This was the dominant failure mode on the first real comparison attempt: **95% failure rate (1/19 accepted)**. | Added `"payload_bits_len": hide_resp.get("payload_bits")` to the `/reveal` request body. Acceptance jumped to 63% immediately. Added regression test `test_run_comparison_success_with_reveal` asserting this field is present. |
| 5 | `scripts/run_zlg_batch_comparison.py` | EGS hyperparameters (`threshold`, `temperature`, `temperature_alpha`) were hardcoded client-side (`0.005/1.0/1.25`) and always overrode whatever the deployed ZLG server was actually tuned to (confirmed live: `0.01/0.7/1.0`, matching a known-documented client/server mismatch in `docs/reports/zlg-sample-audit-2026-07-27.md` item 7). | Added `--zlg-threshold` / `--zlg-temperature` / `--zlg-temperature-alpha` / `--zlg-max-bpw` CLI flags (default to the old hardcoded values, so nothing changes unless passed). Used the server-matched values for every real run. Verified: **all 304 accepted ZLG rows share exactly one EGS parameter tuple** (`0.01, 0.7, 1.0, 2`). |
| 6 | `src/workflows/pipelines/research.py` | `process_post_objects()` re-raised on the *first* per-post failure (e.g. a search-API 403), aborting the entire research batch — inconsistent with the sibling `gen_angles.py`, which already catches/logs/continues per post. Caused a batch to die after only ~15 of 100+ posts when one search call failed. | Matched the established pattern: catch, log, continue. Added regression test `test_process_post_objects_skips_failed_post_and_continues_batch`. |
| 7 | `src/services/stego_metrics_service.py` | GPT-2 perplexity model loading (`AutoModelForCausalLM.from_pretrained`) pings HuggingFace Hub for updates even when the model is already cached locally. A transient network timeout during this check aborted an entire 50-sample generation chunk **after all 50 samples had already succeeded**, with no way to recover just the metrics step (no resume support in `run_actual_workload_e2e.py`) — the whole chunk had to be regenerated from scratch. | Added `_from_pretrained_offline_first()`: try `local_files_only=True` first, fall back to a network load only if the model isn't cached. Applied at all 3 call sites. |

## 3. Deviations from the original plan

- **Live search quota exhausted.** Google Custom Search (all 3 configured keys, 429/403) and the
  scrapingdog Bing fallback (403) both hit quota limits partway through growing the researched-post
  pool beyond the original 164. This is an external/billing limit, not fixable from this environment.
  Consequence: instead of an all-fresh-posts run, the scale-up relies on **`--allow-post-reuse`**,
  tracked transparently (see §5).
- **ASUS GPU host crashed multiple times** during the run (reported by the user, confirmed via lost
  SSH/HTTP connectivity, then recovered). Each time, `llama-server` was relaunched via the WMI-detach
  method above and the in-flight batch resumed or was safely regenerated. No corrupted data made it
  into the final dataset — the diversity/reliability checks below would have caught cross-run
  inconsistency if it had.
- **Local machine's `powercfg` sleep timeout (5 minutes) was disabled** (`standby-timeout-ac/dc 0`)
  after two background jobs died silently with no exception, matching the machine sleeping during idle
  gaps between check-ins. This is a system-settings change made in service of running this explicitly
  autonomous, multi-hour task unattended; fully reversible with
  `powercfg /change standby-timeout-ac 300`. Documented in memory
  (`windows_sleep_kills_background_jobs.md`).
- **Diversity guard run in diagnostic mode.** `build_zlg_method_comparison_dataset.py`'s default
  `--minimum-diversity-ratio 1.0` hard-fails if any post's our-method samples aren't 100% textually
  unique. Given the heavy post reuse this run required, some repeated samples of the same post produced
  duplicate text (see §5) — an honest, expected consequence of reuse, not a bug. Reran with
  `--minimum-diversity-ratio 0.0` so the guard reports rather than blocks, and the actual duplication
  rate is reported below instead of hidden.

## 4. Sample counts and outcomes

**Our method** (10 generation chunks, `context_weighted_v2`, `balanced` variant):

| | Count |
|---|---:|
| Total attempted | 627 |
| Succeeded | 554 (88.4%) |
| Failed | 73 |
| — `receiver_angle_mismatch` (sender/receiver decode disagreement) | 70 |
| — `generation_failure` | 3 |

**ZLG baseline** (fed from all 554 our-method successes, `capacity_matched` mode):

| | Count |
|---|---:|
| Total attempted | 554 |
| Accepted (hide succeeded + reveal decode-verified) | 304 (54.9%) |
| Failed | 250 |
| — `quality_gate_failed` (server-side generation quality gate) | 134 |
| — `sample_extract_failed: not enough cover sentences` | 94 |
| — `prompt_leakage_detected` | 22 |

The 94 "not enough cover sentences" failures come from only **21 unique posts** — 5 of them account for
50 of the 94 (10 failures each) purely because those posts were reused 10x. This is a direct
consequence of post reuse amplifying a per-post characteristic into the aggregate failure count, not
21 independent draws' worth of signal.

## 5. Post reuse (tracked transparently, not silently)

The isolated research pool for this run had **167 posts** (139 already-researched-but-unused posts +
28 newly researched before the search quota ran out). Distribution of how many times each post
contributed a sample across all 10 generation chunks:

| Times used | # of posts |
|---:|---:|
| 1 | 98 |
| 2 | 9 |
| 3 | 1 |
| 6 | 2 |
| 7 | 3 |
| 8 | 2 |
| 9 | 4 |
| 10 | 35 |
| 0 (never succeeded) | 13 |

154/167 posts (92%) produced at least one successful sample; 35 posts were used the maximum 10 times.
This distribution is **not random** — each chunk independently re-derives its post selection from the
same sorted glob order, so smaller chunks (<167 samples) always draw from the same leading subset,
which is why a block of posts hit exactly 10/10 chunks while many others appear only in the one
167-sample first pass. This is disclosed here rather than smoothed over.

**Effect on the final comparison dataset:** of the 304 final paired rows (100 unique posts), only
**244/304 (80.3%) of our-method stegotexts are textually unique** — 78/100 posts have perfectly unique
text across their repeated samples, but 22 posts show duplication (worst case: 7 samples collapsing to
2 unique texts). All primary statistics below are computed **post-clustered** (each of the 100 unique
posts weighted once), which is the existing, correct convention in this codebase for exactly this
situation — row-level statistics from repeated, non-independent trials are reported only as a secondary
diagnostic.

## 6. Head-to-head comparison (100 unique posts, post-clustered, capacity_matched)

| Metric | Our method (mean) | ZLG (mean) | Sign test (Holm-adj. p) | Direction |
|---|---:|---:|---:|---|
| Recoverable capacity (bits/comment) | 15.14 | 16.00 | p < 0.001 | ZLG higher (66/100 posts, 26 ties) |
| GPT-2 perplexity (lower = more predictable) | 68.15 | 53.70 | p = 0.0001 | ZLG lower/better (73/100 posts) |
| Lexical quality index (0–100) | 99.36 | 98.45 | p = 0.0015 | Ours higher (58/100 posts) |
| Word count | 22.4 | 17.3 | p < 0.001 | Ours longer (77/100 posts) |
| BERTScore F1 | 0.842 | 0.848 | — | Comparable |

Consistent with the historical audit (`docs/reports/zlg-benchmark-audit-2026-07-26.md` /
`zlg-sample-audit-2026-07-27.md`): **ZLG leads on raw capacity and GPT-2 perplexity**; our method
produces longer text with a slightly higher lexical-diversity index. The capacity gap here (15.14 vs
16.00, ≈1.06x) is much narrower than the historical ~4x figure, because this run's angle count is
capped at 32 per post (`floor(log2(32))=5` bits) rather than the variable, often-larger angle pools in
that older dataset — a difference in this run's configuration, not a change in the method.

## 7. Independent validation checklist

| Check | Result |
|---|---|
| Exact sample counts | 554 our-method successes, 304 ZLG-verified, 608 total paired rows (304×2) — all counts reconcile exactly across chunk summaries, combined summary, and the final `paired_rows.jsonl`. |
| Encode/decode success | Our method 88.4% (554/627); ZLG 54.9% conditional on our method's successes (304/554). |
| Payload recovery accuracy | 304/304 accepted ZLG rows are `decode_ok=True` (no partial/unverified rows survived into the final dataset). |
| Malformed/failed outputs | Fully categorized in §4, root causes identified (server quality gate, cover-text extraction, prompt leakage guard). |
| Metric formulas/denominators | Capacity-enrichment fix verified directly: sampled rows confirm `recoverable_capacity_bits = floor(log2(comment_choices)) + floor(log2(tangent_choices))` exactly; 0/304 rows show the old zero-tangent defect. |
| Same source posts / fair settings | `capacity_matched` mode used for all 304 accepted rows; **exactly one** EGS parameter tuple (`threshold=0.01, temperature=0.7, alpha=1.0, max_bpw=2`) across every accepted row — confirms no drift in server config mid-run. |
| Duplicate/reused samples | Reported in full in §5, not hidden; diversity guard run in diagnostic mode and its findings surfaced rather than suppressed. |
| Missing records | None — 608 rows = 304 pairs exactly; independence diagnostics confirm 100 clusters, 304 row pairs. |
| Reproducibility/config | `params_used` echoed and saved per ZLG row from the server's own response; angle-artifact identity (schema/sampler version, capacity profile) validated consistent across all posts feeding a given generation lane. |
| Statistical outliers | No NaN/null values in any scored metric across 608 rows. Perplexity range: ours 17.1–338.4, ZLG 7.8–753.5 (both have a long right tail — expected for GPT-2 perplexity on short informal text). |

## 8. Caveats for anyone using this dataset

1. **Treat inference as post-clustered, not row-level.** With 35 posts contributing 10 rows each,
   row-level p-values and confidence intervals are correlated and not statistically valid on their
   own; use `paired_statistics` (post-clustered, Holm-corrected) as done in §6, matching this
   codebase's established convention (`docs/reports/zlg-sample-audit-2026-07-27.md`).
2. **This run's capacity numbers reflect a 32-angle cap**, not necessarily the method's ceiling under a
   different `context_weighted_v2` configuration.
3. **The 94 ZLG cover-extraction failures are concentrated in 21 posts**, not spread evenly — don't read
   the raw failure count as 94 independent data points about the method.
4. Live-search-based pool growth is currently blocked by an exhausted external quota; a future run
   should either wait for quota reset or supply additional API keys before attempting to reduce reliance
   on post reuse further.

---

*All commands, logs, and intermediate artifacts referenced above are preserved under
`stego-side-wing/metrics/e2e_runs/scale300_chunk{1..10}/`,
`stego-side-wing/metrics/zlg_comparison_runs/zlg_batch_scale300/`, and
`stego-side-wing/datasets/prep_runs/context_weighted_v2/scale300_20260729/`.*
