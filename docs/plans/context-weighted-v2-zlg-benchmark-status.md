# context_weighted_v2 vs ZLG Benchmark — Working Status

Status: generation lane blocked; no paired LUCID-vs-ZLG claim is authorized
Last updated: 2026-08-03

Working notes for validating the
[context-conditioned weighted angle sampler](context-conditioned-weighted-angle-sampler-spec.md)
against the ZLG baseline. This is a run log and decision record, not a spec.

## 1. Objective

Generate samples from our method with `WORKFLOW_CONTEXT_SAMPLER=context_weighted_v2` and compare
them against the upstream ZLG baseline (live service:
`D:\Master\code\stego\zero-shot-GLS` on this ASUS host; codec reference:
`tmp_zero_shot_gls_official`).

Target size is 300 samples. Current agreement is to validate on a 25-sample batch first, because
the end-to-end path had never been run with this sampler before.

> **Superseding status.** The active LUCID generation artifact is now the
> 500-attempt balanced lane at
> `metrics/e2e_runs/LUCID_context_weighted_v2_balanced_500`. It stopped at 381
> succeeded / 119 failed, across 64 reused source posts. The artifact's Git
> fields are blank and its worktree was dirty, so it cannot be assigned an
> exact code version; the closest pre-run committed base is `0353848` on
> `fix/zlg-benchmark-overhaul`. The latest recalibrated ZLG run (510/554) uses
> a different, older source summary and is not a paired comparison. See
> [the current research-state report](../reports/2026-08-03-current-research-state.md).

## 2. Environment

**Canonical host:** this workspace is on the ASUS GPU desktop
(`D:\Master\code\stego`). All model backends are local. Pre-move notes that
assumed an OMEN client + LAN `192.168.100.136` / `ssh asus` are obsolete for
operations (they remain accurate as history).

| Endpoint | What | Used by |
|---|---|---|
| `127.0.0.1:8081` | LM Studio, serving `qwen/qwen3.5-9b` | our method (`LM_STUDIO_URL`, `WORKFLOW_LLM_BACKEND=lm_studio`) |
| `127.0.0.1:8090` | `llama-server` | backend for the ZLG API |
| `127.0.0.1:9000` | `zero-shot-GLS/scripts/stego_api_server.py` (`/hide`, `/reveal`) | ZLG baseline comparison |

Start `:8090` / `:9000` from `D:\Master\code\stego\zero-shot-GLS`
(`start_llama.bat` / `start_stego_api.bat`). Detached WMI spawn is only needed
when launching from a short-lived remote session; on-box interactive starts can
use the bat files directly.

### GPU contention (important)

The GPU is a single RTX 5060 Ti (16 GB). `llama-server` (8090) and LM Studio (8081) both want a
9B model resident and together they saturate VRAM. When both try to load, LM Studio returns:

```text
400 Bad Request  {"error":"Model is unloaded."}
```

which surfaces as a fatal `RuntimeError` in the stego pipeline and fails every sample. This cost
one full run early on. Mitigation: keep the two phases sequential — generation (8081) first, ZLG
comparison (8090/9000) second. If a model is already resident and warm, both can coexist; the
failure happens on *load*, not on steady-state serving.

## 3. Pipeline (three stages, all needed)

```text
stage A  angle regeneration        -> per-post tangent codebooks under an isolated dataset root
stage B  stego generation          -> stego samples + summary.json
stage C  ZLG comparison + metrics  -> merged comparison dataset
```

Stage A is required and easy to miss: `scripts/run_actual_workload_e2e.py` only *reads*
pre-existing angle files from `--angles-dir`; it never regenerates them. Running it with
`WORKFLOW_CONTEXT_SAMPLER` set but stale angles on disk silently benchmarks the old sampler.

## 4. What has been achieved

### Stage A — done (25 posts)

New script `scripts/run_context_weighted_angle_batch.py` (thin wrapper over
`WorkflowRunner.run_gen_angles`) regenerates angles under an isolated `WORKFLOW_DATASET_ROOT`, so
the shared `datasets/news_researched` and `datasets/news_angles` corpora are never touched.

```powershell
$env:WORKFLOW_CONTEXT_SAMPLER = 'context_weighted_v2'
$env:WORKFLOW_DATASET_ROOT = 'D:\Master\code\stego\stego-side-wing\datasets\prep_runs\context_weighted_v2\smoke25_20260728'
uv run python scripts/run_context_weighted_angle_batch.py --count 25 --tag context_weighted_v2_smoke25
```

Root: `datasets/prep_runs/context_weighted_v2/smoke25_20260728/`, seeded with 25 already-researched
posts copied from the global `datasets/news_researched`. No live search is needed — research is
frozen per post, matching spec invariant 4.

Quality of the 25 generated codebooks:

- Sampler engaged: `sampler_version: context_weighted_v2`, `schema_version: 3`, namespace
  `selection_channel_angles/context_weighted_v2`.
- Determinism/uniqueness: 25/25 unique `dictionary_id` and `tangent_hash`.
- Angle text is grounded in real extracted source quotes and on-topic; only 1/800 angles shows
  placeholder-derived content, and 0/800 contain mojibake or replacement characters.
- All 25 reached the retained-angle target (`tangent_count` 32, no shortfall).
- Research under-filled on 8/25 posts (5 with no research at all). Comments correctly backfill the
  unused slots per spec 5.4, so this is not a failure, but the 3:1 comment:research weighted
  schedule is barely exercised by this batch.
- Every post recorded `selected_parent_id: null` (root-parent). Expected here, since angle
  pre-generation has no payload driving a parent choice.

### Stage B — done (25 posts), but it benchmarked the wrong code path

Filename mismatch worth remembering: `select_post_ids` in `run_actual_workload_e2e.py` requires
`dataset_dir/<same filename>` to exist and derives `post_id` from the filename stem. Angle files
carry the tag suffix (`1look5n_context_weighted_v2_smoke25.json`) while the baseline corpus has
`1look5n.json`, so every file gets skipped. Worked around by staging a copy under post-id-only
names at `<root>/angles_by_post_id/`, leaving the tagged artifacts intact.

```powershell
$env:WORKFLOW_CONTEXT_SAMPLER = 'context_weighted_v2'
uv run python scripts/run_actual_workload_e2e.py `
  --variant balanced `
  --samples-per-profile 25 `
  --angles-dir datasets/prep_runs/context_weighted_v2/smoke25_20260728/angles_by_post_id `
  --max-retries 1 `
  --run-dir metrics/e2e_runs/context_weighted_v2_smoke25_20260728
```

Note `--variant balanced` is not optional: omitting `--variant` defaults to all eight
`DEFAULT_VARIANTS`, an 8x cost blowup.

Result: 23/25 succeeded (2 ordinary validation-exhaustion failures) at
`metrics/e2e_runs/context_weighted_v2_smoke25_20260728/`.

Observed across the 23 outputs:

| Signal | Value |
|---|---|
| `commentEmbedding.targetType` | `comment` on all 23 |
| picked comment chain depth | 1-10 |
| comment recoverable bits | 10, 11, 12 |
| angle recoverable bits | **5 on all 23** |
| `senderAudit.angles_count` | **32 on all 23** |
| total recoverable bits | 15-17 |

Parent selection works. The tangent channel does not vary at all.

## 5. Key finding — single-frame path bypasses the two-stage channel

The parent-conditioned codebook rebuild *is* implemented, at `src/workflows/pipelines/stego.py:348-362`:
`plan_payload_frames` checks the sampler and calls
`gen_angles_pipeline.preview_post(post, selected_parent_id=parent_id)`, caching by
`(post_id, parent_id)`. `GenAnglesPipeline.preview_post` accepts `selected_parent_id`
(`gen_angles.py:352-358`) and `receiver.py` mirrors it (lines 474, 665, 766).

That logic lives **only on the multi-frame path** (`plan_payload_frames` ->
`encode_payload_frames`). `run_actual_workload_e2e.py:423` calls the single-frame
`StegoPipeline.encode()`, which never routes through it. So stage B exercised parent *selection*
but reused the pre-generated post-level codebook for every parent — exactly the
"silently fall back to a post-level codebook" that spec invariant 3.8 forbids, and it falls back
without any warning.

Corroborating: `senderAudit` carries `dictionary_id`, `angles_hash` and `selected_angle_index`, but
no parent/context/tangent identity fields, though spec section 8 step 6 requires the sender to
persist them.

Consequence: comparing the stage B artifacts against ZLG would measure the old post-level
behavior and understate capacity, since the tangent channel is pinned at 5 bits. The ZLG
comparison was therefore **not** run on this batch.

### Secondary gap — artifact completeness vs spec section 7

Present: schema/generator/sampler versions, dictionary ID, budgets and weights, frozen research
hash, tangent hash/count, recoverable widths, post and selected-parent IDs.

Missing: ordered source identifiers and text hashes; *relevant* and *deduplicated* tangent counts
(only raw-target/retained/final recorded); relationship counts appear once rather than before and
after selection. The ordered-source omission matters most — section 8 has the receiver verify
dictionary identity, and without persisted ordered entry identities a sender/receiver mismatch
must be re-derived rather than diffed.

## 6. Next steps

1. **Build a multi-frame batch wrapper** (in progress). `scripts/run_multi_frame_stego_e2e.py`
   already drives `encode_payload_frames` / `ReceiverPipeline.run_multi_frame`, but it runs a
   single invocation per process and its `--samples` flag is parsed and unused
   (`docs/results/workload-runs-and-artifacts.md:97`). Needs a wrapper that runs N posts and emits
   a `summary.json` with the `entries[]` shape (`post_id`, `sample_index`, `payload_hash`,
   `output_file`) that `scripts/run_zlg_batch_comparison.py` consumes.
2. **Re-run the 25 through the multi-frame path** and confirm the two-stage channel actually
   engages: `selected_parent_id` non-null, tangent width varying per parent, capacity no longer
   pinned at 5 bits.
3. **ZLG comparison** — `scripts/run_zlg_batch_comparison.py --source-summary <summary.json>
   --server-url http://127.0.0.1:9000`. Restart `8090`/`9000` first if they were stopped for
   VRAM headroom, and do this after generation finishes.
4. **Merge and report** — `scripts/build_zlg_method_comparison_dataset.py`.
5. **Scale to 300**, if the 25-sample run validates. Blocker: `datasets/news_researched` holds only
   164 posts, so 300 distinct posts requires researching more first (which *does* need live Google
   Search quota, unlike the frozen-research smoke batch) or reusing posts via `--allow-post-reuse`.
6. **Consider closing the section 7 artifact gaps before the 300-run**, since re-running 300
   samples purely to capture missing provenance fields would be wasteful.

## 7. Open questions

- Should the single-frame path support parent-conditioned codebooks too, or is multi-frame the
  only intended home for the two-stage channel? If single-frame is meant to support it, the silent
  post-level fallback should become an explicit failure per invariant 3.8.
- For the 300-sample target: research more posts to reach 300 distinct, or accept post reuse?
