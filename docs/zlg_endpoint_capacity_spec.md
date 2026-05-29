# ZLG Endpoint Capacity Spec For Fair Comparison

## Problem

The current report has two capacity-looking metrics:

- `embedded_bits`: bits the ZLG encoder says it used in the generated text.
- `payload_bytes_encoded`: UTF-8 payload bytes recovered/accepted.

This is confusing and not fair as the main capacity metric because ZLG includes protocol overhead, currently a 16-bit header. If we send ZLG a 1-byte secret, the API reports `target_bits = 24`: 16 header bits + 8 payload bits. Our method's visible comment embedding reports about 11-14 bits, which appears to exclude the full 49-byte payload channel and does not map directly to ZLG's framed bit count.

For paper-style comparison, capacity should be one metric:

```text
payload_bits_successfully_encoded_under_comment_length_limit
```

This must exclude framing/header overhead unless a separate overhead metric is explicitly reported.

## Comparison Goal

For each original post/comment context, compare:

1. Text quality of our generated comment vs ZLG generated comment.
2. Payload capacity that fits inside a realistic comment-length budget.
3. Same constraints for both methods: same post context, same style examples, same max visible comment length.

The comparison is not "can ZLG encode the full 49-byte payload if allowed to write a long text." The comparison is:

```text
How many payload bits can each method encode while still producing a valid short comment?
```

## Required Endpoint Capability

We need a ZLG capacity-probe endpoint or mode that answers:

Given:

- `prompt`: complete prompt with real comment examples.
- `max_words`: hard visible word limit, e.g. 35 or 40.
- `quality_policy`: same quality gate used for normal `/hide`.
- `max_new_tokens`: generation budget.
- `secret_bits` or `secret_bytes`: candidate payload.
- EGS parameters: `threshold`, `temperature`, `temperature_alpha`, `max_bpw`.

Return:

- Whether encoding succeeded.
- Whether reveal succeeded.
- Exact payload bits successfully recovered.
- Exact framing/header bits used.
- Exact total embedded bits used.
- Generated stegotext.
- Word count and quality metrics.

## Preferred API Design

### Option A: Add `/capacity_probe`

Request:

```json
{
  "prompt": "complete prompt with examples",
  "max_words": 40,
  "quality_max_words": 40,
  "quality_max_retries": 6,
  "payload_bits_candidates": [1, 2, 4, 8, 12, 16, 24, 32, 48, 64],
  "complete_sent": true,
  "max_new_tokens": 64,
  "threshold": 0.005,
  "temperature": 1.0,
  "temperature_alpha": 1.25,
  "max_bpw": 2
}
```

Response:

```json
{
  "best_success": {
    "payload_bits": 12,
    "payload_bytes": 2,
    "payload_bits_exact": 12,
    "header_bits": 16,
    "total_target_bits": 28,
    "total_used_bits": 28,
    "decode_ok": true,
    "secret_matches": true,
    "quality_passed": true,
    "word_count": 31,
    "stegotext": "..."
  },
  "trials": [
    {
      "payload_bits": 8,
      "total_target_bits": 24,
      "success": true,
      "decode_ok": true,
      "quality_passed": true,
      "word_count": 23,
      "used_bits": 24,
      "failure_reason": null
    }
  ]
}
```

### Option B: Extend `/hide`

Add fields:

```json
{
  "secret_bits_base64": "...",
  "payload_bit_length": 12,
  "max_words": 40,
  "return_capacity_accounting": true
}
```

And return:

```json
{
  "payload_bits": 12,
  "header_bits": 16,
  "total_target_bits": 28,
  "used_bits": 28,
  "payload_bits_recovered": 12,
  "decode_ok": true,
  "secret_matches": true,
  "quality_passed": true,
  "word_count": 31,
  "stegotext": "..."
}
```

## Questions For The ZLG Endpoint Agent

1. Does `used_bits` include the 16-bit header?
2. Does `target_bits` always equal `header_bits + payload_bits`, or can it include other codec overhead?
3. Can the API accept raw bit payloads instead of UTF-8 text payloads?
4. If not, can it accept a `payload_bit_length` so we can test non-byte-aligned payloads like 11, 12, or 13 bits?
5. Can the API enforce a hard visible length limit in words, not just quality retry filtering?
6. Can the API return the best valid payload size under a word limit automatically?
7. For failed quality-gate responses, can it return decode-ready candidates as structured data without treating them as successful?
8. Can `/reveal` report payload bits recovered separately from raw extracted bits?
9. What is the exact minimum overhead for any ZLG payload?
10. Is `max_bpw` a hard cap on payload bits per generated token, or only a search/generation constraint?

## Required Report Columns

Use these columns going forward:

- `post_id`
- `sample_index`
- `method`
- `stegotext`
- `word_count`
- `payload_bits_encoded`
- `protocol_overhead_bits`
- `total_embedded_bits`
- `decode_ok`
- `quality_passed`
- `perplexity_gpt2`
- `kl_global_corpus`
- `jsd_global_corpus`
- `repetition_ratio`

Main capacity metric:

```text
payload_bits_encoded
```

Secondary diagnostic only:

```text
total_embedded_bits = payload_bits_encoded + protocol_overhead_bits
```

## Fair Benchmark Procedure

For each sample:

1. Build prompt with real comments from the same post/context.
2. Set hard visible limit, e.g. `max_words = 40`.
3. Ask ZLG to find max recoverable payload bits under that limit.
4. Record the best successful stegotext and metrics.
5. Compare against our method using the same visible-length constraint.

Do not use a fixed 49-byte payload for ZLG unless the question is explicitly "maximum payload with unconstrained long text." That is a different experiment.

