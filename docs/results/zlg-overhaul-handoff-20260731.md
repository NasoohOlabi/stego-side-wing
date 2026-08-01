# ZLG benchmark overhaul — handoff

**Date:** 2026-08-01 (updated; originally 2026-07-31)
**Status:** Workstreams A–D complete and committed. Workstream E is in progress: three
smoke runs found and fixed two further bugs the plan hadn't anticipated (below), and the
full 554-sample re-run is now running against `metrics/zlg_comparison_runs/zlg_batch_scale300_recalibrated`.

Read [`zlg-benchmark-failure-taxonomy-20260731.md`](zlg-benchmark-failure-taxonomy-20260731.md)
first — it is the evidence base for every decision below. The original plan is at
`C:\Users\OMEN\.claude\plans\write-a-plan-to-calm-music.md`.

## One-paragraph background

The dashboard reported 554 ZLG trials / 54.9% acceptance. That number was wrong three
ways: 94 rows (17%) were a bug in our own harness where no HTTP request was ever sent,
133 (24%) were server quality-gate rejections that each got exactly one attempt because
the client never retried, and the gate itself rejected 23.3% of *human* Reddit comments
(it failed any text containing a curly apostrophe, or repeating one word-bigram).

## Where the work lives

Two repos, two branches, both committed, **nothing pushed**.

### `stego-side-wing` — branch `fix/zlg-benchmark-overhaul` (off `refactor/maintainability-phase-0`)

| SHA | What |
| --- | --- |
| `2549565` | Cover sentences back-fill from post title/selftext (fixes the 94) |
| `ae2ad6a` | Retry hide failures a fresh draw could survive + per-attempt prompt reseeding |
| `38f0e92` | `build_api_prompt` no longer primes quoted/markdown output (prompt-only commit) |
| `48ca0ab` | Typed `failure_stage` on every result |
| `6ed3c5f` | `services/naturalness_gate_service.py` — one gate judging both methods |
| `86a93e7` | Failure taxonomy report |
| `251f0ad` | Why the mirror's repetition limit stays at 0.28, below the server's 0.65 |
| `f0d7423` | Server-side work recorded + why the client stays fail-closed |
| `9e4aa02` | Client no longer re-imposes the 40-word cap the server dropped |
| `64630b4` | **Supersedes `38f0e92`.** First smoke run found `38f0e92` made output worse: ending the prompt on a rules block made the model continue writing rules (4/4 probed samples were instruction echo). Fixed by ending on an example instead. |
| `06aa33f` | `instruction_echo` naturalness-gate rule — the echoes above are fluent and passed every other rule |
| `423b6c6` | `stegotext_has_prompt_leakage` reuses the echo patterns, catching them at hide time, not only at analysis time |
| `b886574` | **The other smoke-run bug.** A corrupted `/reveal` (400, content-dependent bit corruption) returned immediately instead of retrying, unlike its sibling failure paths. 10/33 samples in one smoke run were lost to this alone. |

### ZGLS service repo on `asus` — `D:\Master\code\zero-shot\zero-shot-GLS`, branch `main`

| SHA | What |
| --- | --- |
| `ccff266` | Threshold recalibration: non-ASCII → `replacement_char_count`, bigram ≤1 → ≤3, `max_words` 40 → 60 |
| `5c4bcaf` | Matching test updates |
| `5246767` | `/health` reports `git_commit`, `git_dirty`, full active threshold set |
| `11cab87` | Human-corpus calibration test (50 comments, 72% → 0% rejected) |

Remote suite: **46 passed, 6 skipped** (skips need a live `ZGLS_SERVER_URL`).
`src/llama_server.py` and the `.ps1`/`.bat` launchers are deliberately untouched/unstaged.

### `stego-results-viewer`

`db53479` — acceptance reported over attempts that actually reached the server;
consumes `failure_stage` instead of regex-parsing a status code out of an error string.

## Server state right now

Both services are **up**, started detached via `Invoke-CimMethod Win32_Process Create`
(a one-shot SSH `Start-Process` dies with the session — see
`asus_remote_process_detachment` memory). Sleep is disabled via `powercfg`.

- `llama-server` on `:8090`, model `Qwen3.5-9B-Q4_K_M.gguf`
- ZGLS API on `:9000`, verified via `GET http://192.168.100.136:9000/health`:
  `git_commit: 11cab87`, `quality_max_words: 60`, `quality_max_bigram_repeat: 3`.
  `git_dirty: true` is expected (untracked launchers + the `cache_prompt` line).

Restart recipe if they die:

```powershell
# from a local PowerShell
$s = 'Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = "cmd.exe /c D:\Master\code\zero-shot\zero-shot-GLS\start_llama.bat"; CurrentDirectory = "D:\Master\code\zero-shot\zero-shot-GLS" }'
$e = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($s))
ssh asus "powershell -NoProfile -EncodedCommand $e"
# then the same with start_stego_api.bat, after :8090 is listening
```

**SSH gotcha:** the remote session lands in `cmd.exe`, so pipes and quotes get mangled.
Always send work as `powershell -NoProfile -EncodedCommand <base64 UTF-16LE>`. Anything
over ~8 KB exceeds the cmd command-line limit — `scp` the file instead.

## What is left (Workstream E)

### Smoke-run history (why three, not one)

1. **25 samples, `38f0e92` prompt.** 2 of the first 3 rows were the model continuing the
   prompt's own rules instead of writing a comment — one was recorded *accepted*. Traced
   to the prompt ending on instructions rather than an example; `/hide` is a raw
   completion, so the model continues whatever the prompt ends with. Fixed by `64630b4`
   (prompt) + `06aa33f`/`423b6c6` (gate catches any that still slip through). Verified with
   a direct probe: the old shape was 4/4 instruction echo, the fixed shape 4/4 real
   comments.
2. **25 samples, fixed prompt.** Instruction echo was gone, but **10/33 rows (30%) failed
   at `/reveal`** with `payload utf-8 decode failed` — hide succeeded, reveal extracted
   different bits. The old run had *zero* of these across 554 samples. A direct
   hide/reveal round-trip probe (12 pairs, holding only the prompt's trailing whitespace
   as a variable) found **zero** failures, ruling out the prompt and pointing at the
   client: `reveal_decode_failed`/`reveal_payload_mismatch` already retried, but a
   *reveal exception* (this 400) returned immediately regardless of `max_retries`. Fixed
   by `b886574`.
3. **25 samples, both fixes.** **23/25 accepted (92%)**, zero `harness_extract`, zero
   `leakage_check`, zero reveal failures. The 2 failures both exhausted 5 quality-gate
   retries genuinely. One accepted row contained a fabricated error-JSON artifact
   (`{"index": "error": true}... [ERROR: Failed to call LLM.`) that no current rule
   catches — noted below, not blocking.

Command used each time (swap `--limit 25` for a full run):

```bash
cd d:/Master/code/stego/stego-side-wing
uv run python scripts/run_zlg_batch_comparison.py \
  --source-summary metrics/e2e_runs/scale300_combined_summary.json \
  --server-url http://192.168.100.136:9000 \
  --run-dir metrics/zlg_comparison_runs/zlg_smoke_recalibrated \
  --limit 25
```

### Full re-run — in progress

Launched 2026-08-01 against the **combined** summary (all 554 entries, 154 posts, one
command rather than a ten-chunk loop):

```bash
uv run python scripts/run_zlg_batch_comparison.py \
  --source-summary metrics/e2e_runs/scale300_combined_summary.json \
  --server-url http://192.168.100.136:9000 \
  --run-dir metrics/zlg_comparison_runs/zlg_batch_scale300_recalibrated
```

Check `metrics/zlg_comparison_runs/zlg_batch_scale300_recalibrated/progress.json` for
status. The runner resumes on `source_key`, so re-invoking after an interruption appends
rather than duplicating — if it died, just run the same command again. Record the
`/health` block alongside the run once done.

### 3. Rebuild the dataset

`--source-summary` and `--dataset-dir` default to an unrelated older run
(`fresh_metrics_200_*`) — they must be passed explicitly or the build silently pairs
against the wrong posts:

```bash
uv run python scripts/build_zlg_method_comparison_dataset.py \
  --zlg-run-dir metrics/zlg_comparison_runs/zlg_batch_scale300_recalibrated \
  --source-summary metrics/e2e_runs/scale300_combined_summary.json \
  --dataset-dir metrics/e2e_runs/scale300_combined_dataset
```

### 4. Re-verify the gate calibration against the new output

```bash
uv run python scripts/calibrate_naturalness_gate.py --zlg-run-dir <run-dir>
```

Exits non-zero above the 5% human-rejection budget. Current baseline: **4.96%** human,
and 22/304 (7.2%) of the old ZLG acceptances now rejected — all on `structural_artifact`.

### 5. Point the dashboard at the new run

`stego-results-viewer/src/app/zlg-comparison/page.tsx` — `RUN_DIR` near line 122.

### 6. Update the numbers

- `docs/results/zlg-benchmark-failure-taxonomy-20260731.md` — the "Corrected figures"
  table currently holds re-analysis of the *old* run (554 → 460 trials, 54.9% → 66.1%,
  8.78 → 10.57 bits/attempt). Replace with real re-run figures.
- `project_paper` — wherever acceptance rate and bits-per-attempt are cited.

## Decisions already made — do not redo these

- **Do not switch the client to `enforce_quality=false`.** The flag exists, but the gate
  sits inside the server's retry loop, so disabling it makes ZLG accept its first draw
  and lose all four quality retries — it would *disadvantage* the baseline. It also
  reports `quality_passed=true` for gate-failing text. Full reasoning in the taxonomy doc.
- **Do not loosen the mirror's `repetition_ratio_limit` to match the server's 0.65.** It
  looks like drift but is deliberate: with the bigram limit relaxed, 0.65 passes genuinely
  degenerate output. There is a test pinning this (`test_degenerate_repetition_still_fails`).
- **Do not modify the EGS codec, sampling, or bit-packing.** Agreed scope is prompt and
  decode configuration only, so the baseline algorithm stays defensible in the paper.

## New follow-up found in smoke run 3, not yet fixed

One accepted sample's stegotext was `{"index": "error": true}, {"content": null}]
[ERROR: Failed to call LLM.` — a fabricated API-error blob, not an instruction echo,
`<think>` block, or markdown. No current `naturalness_gate_service` rule catches it
(it doesn't start with `[`, so `structural_artifact`'s `^\s*\[` miss it). Seen once in
23 accepted samples so far. Worth watching in the full run's output before deciding
whether it needs its own rule or is rare enough to ignore.

## Not done, and why

- **Thinking mode is still on.** The plan called for disabling it, but the service uses
  the raw completion endpoint — there is no chat template and no `enable_thinking` flag to
  set. Suppressing `<think>` would mean editing the baseline's own
  `config/workflow_llm_prompts.json` system template, which is a deeper baseline
  modification than the agreed scope. The analysis gate catches `<think>` as a
  `structural_artifact`, so no result is wrong; ZLG just wastes retries. Decide explicitly
  before the full run.
- **Server `quality_passed` semantics.** It carries the overall acceptance decision rather
  than the naturalness verdict. Harmless while we run fail-closed; would silently mislabel
  everything for anyone using `enforce_quality=false`.
- **Server `quality_max_repetition_ratio` is still 0.65.** Same gap as above — the server
  will now spend retries emitting text the analysis gate rejects.

## Pre-existing failures — not ours, don't chase them

- `uv run pyright`: 1 error in `src/services/stego_metrics_service.py:322`
  (`reportUnnecessaryCast`) — from the uncommitted refactor work on the parent branch.
- `uv run ruff check .`: 6 errors, all in `extreact_searched_values.py` at repo root.
- `pnpm typecheck` in the viewer: errors in `admin-api/*` and
  `zlg-comparison/_components/metric-bar-chart.tsx` — all in files modified but not
  committed by this work. `page.tsx`, which this work did change, typechecks clean.

All ZLG-related files pass `ruff` clean, and the full `uv run pytest -q` suite is green.
