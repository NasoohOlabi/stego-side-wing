# Method, ZLG Baseline, and ASUS GPU Workflow

## Our method: visible selection channel

`stego-side-wing` hides a payload by selecting ordinary visible linguistic
choices rather than inserting invisible Unicode or modifying token sampling.
For a source post, the payload selects a target parent/comment and an angle.
The candidate engine generates or revises a visible reply, then accepts only a
candidate whose angle can be decoded from it. `ReceiverPipeline` locates the
parent context and decodes the candidate angle using the same codec contract.

Key implementation boundaries:

- `src/workflows/pipelines/stego.py` orchestrates the sender.
- `src/workflows/pipelines/receiver.py` rebuilds context and decodes.
- `src/workflows/utils/stego_codec.py` is the shared sender/receiver contract.
- `src/services/zlg_comparison_service.py` runs the official-baseline lane.

In the current lossless selection mode, recoverable capacity for one frame is

```text
floor(log2(comment choices)) + floor(log2(tangent choices))
```

This is intentionally lower than the physical index width when a choice count
is not a power of two: only uniquely decodable selections count. Larger
payloads require multiple independently recoverable frames/carriers. Do not
claim padding or aliased bit patterns as useful payload.

## Comparison design

The vendored reference implementation lives in `tmp_zero_shot_gls_official`;
do not modify it for normal project work. A comparison must use a recovered or
implemented, versioned service that preserves the official token-probability
hide/extract behavior—not a generic text-generation endpoint. ZLG embeds bits
across generated tokens, so it has a different and potentially much higher
capacity profile than this selection channel. See
[`docs/zlg_endpoint_capacity_spec.md`](../docs/zlg_endpoint_capacity_spec.md)
for the service contract and accounting requirements.

Use the paired publication runner for research-grade comparisons:

```powershell
uv run python scripts/run_publication_benchmark.py `
  --manifest <frozen-manifest.json> `
  --angles-dir metrics/benchmark/prepared_angles `
  --run-dir metrics/benchmark/runs/<run-name> `
  --stage pilot `
  --comparison-mode capacity_matched `
  --zlg-server-url http://127.0.0.1:9000
```

`capacity_matched` sends the same useful payload to each method. Use
`max_capacity` only as a separate capacity experiment, never as evidence that
one method is more fluent at equal payload. Keep failed generations and decode
failures in the attempt data; report both intention-to-treat and
successful-output-only results. With `--stage pilot`, the runner evaluates the
first 25 frozen posts; `--stage auto` expands to all 100 only after its
predeclared gate passes. A normal run requires a clean worktree, a manifest of
at least 100 unique posts, and matching frozen angle hashes.

## ASUS desktop: run the GPU ZLG service

The SSH host alias `asus` connects to the higher-GPU desktop. Use it only for
the ZLG baseline repository at `D:\Master\code\zero-shot\zero-shot-GLS`.
The remote `D:\Master\code\stego-side-wing` checkout is abandoned: never use,
update, or inspect it. This workspace remains the authoritative location for
our method and every comparison runner. Connect interactively with:

```powershell
ssh asus
```

The remote OpenSSH session starts under `cmd.exe`; start PowerShell and move
to the checkout before running repository commands:

```powershell
powershell -NoProfile
$ZlgRoot = 'D:\Master\code\zero-shot\zero-shot-GLS'
Set-Location $ZlgRoot
```

Start the versioned ZLG service from `$ZlgRoot` using the protocol and model
settings documented in [`STEGO_API_SERVER.md`](../../STEGO_API_SERVER.md).
The local `llama-server` backend must already be running. In a second remote
PowerShell, verify the service before starting a benchmark:

```powershell
Invoke-RestMethod http://127.0.0.1:9000/health
```

Run the paired benchmark from this local `stego-side-wing` checkout, pointing
`--zlg-server-url` at the remote service and supplying
`--zlg-server-version <commit-or-image-digest>` if `/health` does not return a
version. Prefer an SSH tunnel rather than exposing the service on the network:

```powershell
ssh -N -L 9000:127.0.0.1:9000 asus
```

With that tunnel open, the local runner can use
`--zlg-server-url http://127.0.0.1:9000`.

Before any long run, record the remote Git revision, model identifier, service
version/digest, GPU configuration, and exact command. The benchmark requires
`/health` to identify the loaded model and a service version (or an explicit
`--zlg-server-version`). Copy the resulting run directory back before opening
it in `stego-results-viewer`; preserve `attempts.jsonl`, summaries, manifest,
and `zlg_server_identity.json` together. The run artifacts are generated
locally, so no copy-back from the abandoned remote checkout is needed.

## Metrics: what they mean

| Metric | Interpretation | Reporting caution |
| --- | --- | --- |
| Useful recovered bits / bits per word | Verified hidden information and efficiency | Count only payload recovered by the real receiver; show failures separately. |
| Generation success and exact recovery | Reliability of producing and decoding a carrier | Failure is method performance, not missing data. |
| Perplexity | Causal-LM predictability proxy | It is not human-likeness; report median and token-weighted values alongside a mean when possible. |
| KL / JSD | Unigram distribution shift from matched post or corpus | Strongly affected by comment length, vocabulary sparsity, smoothing, and corpus leakage; use length-matched held-out controls. |
| BLEU / ROUGE / BERTScore | Lexical/semantic similarity to a selected human reference | An open-ended Reddit reply has many valid answers, so these are proxies, not naturalness ground truth. |
| G-Eval and thread-grounded factuality | Judge-scored quality and support by thread evidence | Cache prompts/model/version, randomize paired order, and treat factuality as thread evidence—not canonical FActScore. |
| Synthetic detection rate | How often a blinded judge or detector labels output synthetic | Preserve the exact protocol and aggregate by source post; lower is preferable. |
| Passive-detector ROC-AUC | Classifier separability of human and stego text | Split by source post to prevent carrier leakage across train/test; 0.5 is chance-level. |
| Robustness recovery | Decode success after deletion, substitutions, reorder, paraphrase, or context mutation | Attack with the real corresponding receiver and retain attack settings. |

Report synthetic-detection rate and passive-detector ROC-AUC separately. The
former is a rate from a fixed detection protocol; the latter measures a
classifier's separability, where 0.5 is chance-level. Both must be split or
aggregated by source post to avoid carrier leakage.

The current historical audit is `docs/reports/zlg-benchmark-audit-2026-07-26.md`.
It covers 304 accepted pairs aggregated into 47 independent post clusters. In
that artifact, ZLG led on conditional capacity, GPT-2 perplexity, KL/JSD, and
unadjusted G-Eval; our method had lower synthetic detection. These are not
final general claims: the arms had unequal trial structure, our outputs
repeated across trials, and several metrics are length-confounded. Prefer a
fresh symmetric, frozen-manifest rerun.

The sample-level audit is `docs/reports/zlg-sample-audit-2026-07-27.md`, with the
measured baseline dossier in `docs/reports/zlg-baseline-weaknesses-2026-07-27.md`
and reproducible statistics from `scripts/audit_paired_sample_artifacts.py`. It
corrects the capacity figure: the enrichment step in
`scripts/build_zlg_method_comparison_dataset.py` reads a post snapshot without
`angles`, so the stored `selection_bits` covers only the comment channel.
Corrected post-clustered recoverable capacity for that run is **18.66
bits/comment, not 10.66**. Quote the corrected figure, and do not read
`selection_bits` from that artifact until the enrichment defect is fixed.
