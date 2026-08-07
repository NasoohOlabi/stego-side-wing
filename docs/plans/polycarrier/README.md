# POLYCARRIER — Plan

**Codename:** `POLYCARRIER`  
**Status:** implementation started (sample-layer aggregator)  
**Created:** 2026-08-06  
**Lane:** batch `/zlg-comparison` (same metrics chain as `zlg_batch_scale300*`)

## Objective

Benchmark our **multi-comment / multi-frame selection-channel embedding** against the
official ZLG baseline on large payloads (default **256 useful bits / 32 UTF-8 bytes**),
and compute **every metric already used by the zlg-comparison dashboard** — correctly
aggregated for a payload that spans multiple independent stego comments (frames).

A single selection-channel frame typically carries ~15–20 recoverable bits. Large
payloads require several frames (`encode_payload_frames`), often across multiple posts.
POLYCARRIER is the operational plan to generate those carriers, capacity-match ZLG per
carrier, score the full metric suite, and roll results up at the **sample** (whole
payload) level as well as the existing **frame/comment** level.

## Why this is not the single-frame e2e path

`scripts/run_actual_workload_e2e.py` calls single-frame `encode()` and understates
multi-frame capacity. POLYCARRIER uses:

| Stage | Script |
|---|---|
| Ours multi-frame | `scripts/run_multi_frame_batch_e2e.py` → `StegoPipeline.encode_payload_frames` |
| ZLG paired | `scripts/run_zlg_batch_comparison.py --comparison-mode capacity_matched` |
| Score + pair | `scripts/build_zlg_method_comparison_dataset.py` |
| Optional judges | `scripts/run_codex_judge_campaign.py` |
| View | `stego-results-viewer` `/zlg-comparison` via `ZLG_RUN_DIR` |

Publication (`run_publication_benchmark.py` / `attempts.jsonl`) is a separate protocol
and is **out of scope** for this codename.

## Plan documents

| File | Role |
|---|---|
| [`execution-plan.md`](execution-plan.md) | Phased commands, env, GPU sequencing, run-dir names |
| [`metrics-multicomment.md`](metrics-multicomment.md) | How to implement and calculate **every** zlg-comparison metric against multi-comment embedding |
| [`progress.md`](progress.md) | Running status log; newest entry on top |

**Sample-layer aggregator (code):** `src/services/polycarrier_sample_metrics.py` +
`scripts/aggregate_polycarrier_sample_metrics.py`

## Related specs

- [`../efficient-multiframe-selection-channel-spec.md`](../efficient-multiframe-selection-channel-spec.md) — channel model
- [`../../../.agents/method-and-zlg-benchmark.md`](../../../.agents/method-and-zlg-benchmark.md) — method + on-ASUS GPU workflow
- [`../../results/zlg-overhaul-handoff-20260731.md`](../../results/zlg-overhaul-handoff-20260731.md) — batch rebuild commands (paths predate workspace move; use `D:\Master\code\stego\zero-shot-GLS`)
- [`../../zlg_endpoint_capacity_spec.md`](../../zlg_endpoint_capacity_spec.md) — ZLG capacity accounting

## Operating rules

- This whole workspace is on ASUS at `D:\Master\code\stego`. Work in sibling
  `stego-side-wing` and `zero-shot-GLS` (`D:\Master\code\stego\zero-shot-GLS`).
  Ignore old `D:\Master\code\zero-shot\zero-shot-GLS` and any abandoned remote
  checkout paths.
- Sequential GPU: LM Studio (ours) first, then ZLG (`llama-server` + `stego_api_server`).
  Do not cold-load both 9B models on the 16 GB card.
- ZLG URL is local: `http://127.0.0.1:9000` (no SSH tunnel / LAN IP required).
- Always pass `--source-summary` and `--dataset-dir` explicitly to
  `build_zlg_method_comparison_dataset.py` (defaults point at a stale run).
- `--dataset-dir` must be posts **with `angles`** (same as `--angles-dir`) so
  `selection_bits` is not comment-channel-only.
- `capacity_matched` for fluency claims; `max_capacity` only in a separate run-dir.
- All repo hard rules apply (forbidden carriers, prompt red line, no `print`, etc.).
