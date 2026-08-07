# ZGLS API comparison report

Date: 2026-05-27  
Reporter environment: `D:\Master\code\stego\stego-side-wing`  
ZGLS API: `http://192.168.100.136:9000` (historical LAN URL; canonical now is `http://127.0.0.1:9000` from sibling `D:\Master\code\stego\zero-shot-GLS` on ASUS)  
API docs endpoint observed: `GET /docs` returned `200`  
OpenAPI title/version observed: `Zero-shot GLS API`, version `0.3.0`

## Summary

I tested the live ZGLS API against payloads produced by an existing steganography benchmark run. The API works for a small secret and reveal decodes correctly, but it fails for the real comparison payload size used by the benchmark.

The current comparison result is:

| System | Payload | Result |
|---|---:|---|
| Main method, `balanced` source run | 49 bytes | succeeded in saved benchmark samples |
| ZGLS API, same first 5 payloads | 49 bytes | 0/5 accepted |
| ZGLS API, capacity probe | 5 bytes | accepted and reveal decoded |
| ZGLS API, capacity probe | 10 bytes | rejected by quality gate |
| ZGLS API, capacity probe | 20 bytes | rejected/truncated |
| ZGLS API, capacity probe | 30 bytes | rejected/truncated |

The main issue is not that `/reveal` is broken. A 5-byte secret successfully round-trips through `/hide` and `/reveal`. The issue appears to be practical capacity under the API's quality gate: once payload size grows, generated text becomes too long, repetitive, malformed, or truncated, and `/hide` returns `422 quality_gate_failed`.

## API schema used

The API schema was fetched from:

```text
GET http://192.168.100.136:9000/openapi.json
```

Relevant schema:

```text
POST /hide
required:
- prompt: string
- secret: string
optional:
- complete_sent: boolean
- cover_texts: string[] | null
- corpus: string | null

POST /reveal
required:
- prompt: string
- stegotext: string
optional:
- context_seed: integer | null
- effective_prompt_hash: string | null
- cover_texts: string[] | null
- corpus: string | null
- stego_token_ids: integer[] | null
```

The successful small-secret reveal used `stego_token_ids`, `context_seed`, and `effective_prompt_hash` returned by `/hide`.

## Test harness

I used the local comparison runner:

```text
D:\Master\code\stego\stego-side-wing\scripts\run_zlg_batch_comparison.py
```

The runner reads source samples from:

```text
D:\Master\code\stego\stego-side-wing\metrics\e2e_runs\fresh_metrics_200_20260509T233342Z\balanced\summary.json
```

For each sample, it extracts:

- the original payload string from the saved main-method output
- cover sentences from title, selftext, and comment chain
- a plain prompt intended for the live API wrapper:

```text
Write one short natural Reddit news discussion comment similar to the provided cover sentences. Output only the comment.
```

I originally tried the paper's full XML/LLaMA-style prompt template, but the live API schema and behavior indicate this wrapper expects a plain task prompt plus `cover_texts`, not a fully expanded ZGLS prompt. With the full paper prompt, 49-byte payloads also failed by truncation.

## Reproduction command

```powershell
$run='D:\Master\code\stego\stego-side-wing\metrics\zlg_comparison_runs\zlg_api_live_20260527_limit5'
python 'D:\Master\code\stego\stego-side-wing\scripts\run_zlg_batch_comparison.py' `
  --server-url 'http://192.168.100.136:9000' `
  --run-dir $run `
  --limit 5 `
  --overwrite `
  --max-retries 1 `
  --request-timeout-seconds 1800
```

Output summary:

```json
{
  "total_entries": 5,
  "processed_entries": 5,
  "accepted": 0,
  "failed": 5
}
```

Result artifacts:

```text
D:\Master\code\stego\stego-side-wing\metrics\zlg_comparison_runs\zlg_api_live_20260527_limit5\summary.json
D:\Master\code\stego\stego-side-wing\metrics\zlg_comparison_runs\zlg_api_live_20260527_limit5\results.jsonl
```

## Same-payload batch result

All five source samples used `payload_bytes_target = 49`. Every call to `/hide` failed with HTTP `422` and response detail `quality_gate_failed`.

| Post | Sample | Payload bytes | API result | Last failure | Decode-ready best attempt |
|---|---:|---:|---|---|---|
| `1nero22` | 0 | 49 | failed | `truncated` | false |
| `1ngylf0` | 1 | 49 | failed | `truncated` | false |
| `1ni3f9u` | 3 | 49 | failed | `truncated` | false |
| `1ni4wz3` | 4 | 49 | failed | `truncated` | false |
| `1nihypp` | 5 | 49 | failed | `truncated` | false |

Representative API error detail:

```json
{
  "reason": "quality_gate_failed",
  "last_fail_reason": "truncated",
  "max_retries": 6,
  "best_metrics": {
    "word_count": 64,
    "repetition_ratio": 0.25,
    "single_token_share": 0.078125,
    "max_bigram_repeat": 2,
    "terminal_punctuation": true,
    "decode_ready": false,
    "retry": 6
  }
}
```

## Capacity probe

I also tested one cover context with increasing secret sizes.

Payloads:

```text
5 bytes:  "hello"
10 bytes: "abcdefghij"
20 bytes: "abcdefghijklmnopqrst"
30 bytes: "abcdefghijklmnopqrstuvwxyz1234"
```

Results:

| Secret bytes | `/hide` status | `/reveal` status | Decode result | Notes |
|---:|---:|---:|---|---|
| 5 | 200 | 200 | success | `secret == "hello"` |
| 10 | 422 | n/a | n/a | `quality_gate_failed`; best attempt was decode-ready but failed quality |
| 20 | 422 | n/a | n/a | `quality_gate_failed`; `last_fail_reason = truncated` |
| 30 | 422 | n/a | n/a | `quality_gate_failed`; `last_fail_reason = truncated` |

Successful 5-byte response:

```json
{
  "payload_bytes": 5,
  "used_bits": 56,
  "target_bits": 56,
  "bpw_estimate": 2.0,
  "quality_passed": true,
  "mode": "huffman",
  "params_used": {
    "mode": "huffman",
    "threshold": 0.001,
    "temperature": 0.8,
    "temperature_alpha": 1.0,
    "max_bpw": 2
  }
}
```

Successful reveal:

```json
{
  "secret": "hello",
  "payload_bytes": 5,
  "decode_ok": true,
  "warnings": [
    "decode used provided stego_token_ids (token-stable path)"
  ]
}
```

## Observed quality issues in rejected attempts

The rejected best attempts were often long, repetitive, and not shaped like normal Reddit comments. Examples of recurring failure modes:

- generated text repeated phrases heavily
- generated text became article-like rather than comment-like
- generated text included malformed punctuation or duplicated sentence framing
- generated text required 50 to 132 words for a 49-byte payload and still did not become decode-ready
- for 10 bytes, the API sometimes produced a decode-ready attempt, but the quality gate still rejected it

This suggests the API's quality gate is doing useful work, but the current embedding configuration cannot carry the tested payload sizes while preserving acceptable text quality.

## Comparison interpretation

The main method's saved benchmark run had already generated successful outputs carrying these payloads. The ZGLS API was tested against the same source payload strings, not easier custom payloads, and failed all five.

The result should not be interpreted as "`/reveal` is broken" or "the API cannot hide anything." It can hide and reveal a small 5-byte secret. The issue is that the API appears to have a much lower practical payload capacity than the tested method under the current quality constraints.

## Questions for maintainers

1. Is a 49-byte UTF-8 secret expected to be within the practical capacity of this API under the default quality gate?
2. Are clients expected to chunk larger secrets across multiple stegotexts?
3. Is there an API-supported way to relax `max_new_tokens`, quality thresholds, or retry count?
4. Is `cover_texts` intended to replace the paper's full XML/LLaMA prompt template, or should clients provide the full prompt and omit `cover_texts`?
5. Can `/hide` return the best rejected candidate, token ids, and metrics in a structured top-level field instead of only inside `422.detail`, so benchmark tooling can analyze failed attempts consistently?
6. Is the current server using the paper's LLaMA2-Chat-7B setup, or a different generator/config?

## Requested clarification

Please clarify the intended maximum payload size and the recommended client request shape for a fair benchmark comparison. In the current API behavior, same-context 49-byte payloads consistently fail quality/truncation, while a 5-byte secret round-trips correctly.
