# Stego Sender To Receiver Pipeline

This document traces the current pipeline from post selection through receiver payload extraction. The important model is:

```text
payload -> protected payload -> compressed bitstream
compressed bitstream prefix -> comment-context selection bits + angle-selection bits
selected angle -> visible stego comment generated to decode back to that angle
receiver decodes visible comment to angle index -> reconstructs bit prefix -> recovers payload
```

The payload is carried by the selection channel, not by invisible characters in the visible `stego_text`.

## Main Files

- `src/workflows/runner.py`: top-level workflow orchestration.
- `src/workflows/pipelines/stego.py`: sender-side stego orchestration.
- `src/workflows/utils/stego_codec.py`: shared sender/receiver bit-layer contract.
- `src/workflows/pipelines/decode.py`: visible text to angle-index decoder.
- `src/workflows/pipelines/receiver.py`: receiver-side comment location, context rebuild, and payload recovery.
- `src/app/routes/api_v1/routes_workflows.py`: API entrypoints for `stego`, `decode`, `receiver`, and `stego-receiver-live`.

## 1. Post Preparation Before Stego

The sender normally starts from an already-prepared post with:

- post metadata such as `id`, `title`, `author`, `url`, `selftext`, `permalink`;
- a nested comment tree under `comments`;
- `search_results` from research;
- generated `angles`, where each angle has fields such as `category`, `source_quote`, `tangent`, and sometimes `source_document`;
- enough deterministic context for the receiver to rebuild the same dictionary and angle list later.

The preparation path is:

1. `WorkflowRunner.run_data_load()` reads posts from `filter-url-unresolved` and resolves/fetches content.
2. `WorkflowRunner.run_research()` enriches the post with search terms and `search_results`.
3. `WorkflowRunner.run_gen_angles()` produces the `angles` list.
4. `WorkflowRunner.run_stego()` or `StegoPipeline.process_post()` consumes a post from `final-step`, falling back to `angles-step` for local compatibility.

The API wrappers are:

- `POST /api/v1/workflows/data-load`
- `POST /api/v1/workflows/research`
- `POST /api/v1/workflows/gen-angles`
- `POST /api/v1/workflows/stego`
- `POST /api/v1/workflows/prep-until-google-quota-then-stego`

## 2. Sender Post Selection

`StegoPipeline.process_post()` is the file-backed sender entrypoint.

Inputs:

- `post_id`: optional explicit post id.
- `payload`: optional payload string. If omitted, the pipeline tries to read the n8n workflow default from `workflows/27rZrYtywu3k9e7Q.json`.
- `tag`: optional output tag.
- `list_offset`: used when auto-selecting a post.

Selection behavior:

1. If `post_id` is missing, `_select_next_post_id()` calls `BackendAPIAdapter.posts_list(step="final-step", count=1, offset=list_offset, tag=tag)`.
2. It takes the first filename and strips `.json`.
3. It loads `{post_id}.json` from `final-step`.
4. If that is missing, it tries `angles-step`.
5. If an explicit `post_id` is stale or missing, it can fall back to auto-selection for the same tag.

The selected post is passed to `StegoPipeline.encode(payload, post, tag)`.

## 3. Sender Payload Transform

`StegoPipeline.encode()` first validates that `post["angles"]` is a non-empty list. Then it applies the configured payload transform:

```python
payload_transform = get_workflow_payload_transform()
embedded_payload = protect_payload(
    payload,
    transform=payload_transform,
    secret=get_workflow_encoding_secret(),
)
```

Supported transforms in `stego_codec.py`:

- `plain`: payload is embedded as-is.
- `hmac_xor_v1`: HMAC-authenticated XOR stream format with prefix `swsec1.`.
- `secure_compact_v2`: zlib-compressed authenticated XOR stream format with prefix `swsec2.`.

The receiver later reverses this with `unprotect_payload()`.

## 4. Dictionary Construction

`augment_post()` calls `build_dictionary(post)`.

That delegates to `build_post_text_dictionary(post, apply_capacity_profile=True)` in `workflows.utils.text_utils`. The dictionary is deterministic and source-aware. It is used only for payload compression and decompression.

The sender also records a `build_dictionary_report()` in `sender_audit`:

- `dictionary_id`
- `dictionary_hash`
- `dictionary_count`
- raw/source counts
- truncation/capacity metadata

The receiver rebuilds the same report and compares it against sender audit when audit is available.

## 5. Payload Compression

`compress_payload(payload, dictionary)` creates the full compressed bitstream.

It tries two encodings:

1. Standard encoding:
   - marker bit `0`
   - then UTF-8 payload bytes as binary

2. Dictionary encoding:
   - marker bit `1`
   - then a sequence of literal chunks and dictionary references
   - literal chunk marker `0`
   - dictionary-reference chunk marker `1`

The function chooses dictionary mode only if it is shorter than standard mode. Otherwise it returns standard mode.

Important fields:

- `method`: `standard` or `dictionary`
- `payload`: the transformed payload
- `compressed`: full compressed bitstring
- `compressedLength`
- `originalLength`
- `ratio`
- `references`

## 6. Bit Capacity: Comment Selection Then Angle Selection

The compressed bitstream is consumed from the front.

`augment_post()` performs:

1. `embed_in_comment_selection(compressed, post)`
2. `embed_in_angle_selection(remaining_bits, nested_angles)`

The selected bits are:

```text
selection_signature = comment_bits + angle_bits
```

### 6.1 Comment-Selection Bits

`embed_in_comment_selection(bits, post)`:

1. Flattens `post["comments"]`.
2. Let `n = len(flattened_comments)`.
3. Computes `bits_count = get_bit_width(n)`.
4. Takes that many bits from the compressed bitstream.
5. Converts those bits to `selection_index`.
6. If `selection_index == 0`, the generated stego reply targets the post context.
7. If `selection_index > 0`, it selects `flattened_comments[selection_index - 1]`.
8. It walks parent ids upward to build `pickedCommentChain`.

The comment selection returns:

- `bitsUsed`
- `bitsCount`
- `targetType`: `post` or `comment`
- `context`: post-level context
- `pickedCommentChain`
- `remainingBits`
- `insufficientBits` when padding was needed

### 6.2 Angle-Selection Bits

`embed_in_angle_selection(bits, nested_angles)`:

1. Flattens nested angle groups.
2. Adds canonical `idx` to each flattened angle.
3. Computes `bits_count = get_bit_width(len(angles) - 1)`.
4. Takes that many bits.
5. Converts to `idx`.
6. If `idx >= len(angles)`, it wraps with modulo.
7. Returns the selected angle first, followed by the remaining angles.

The angle selection returns:

- `bitsUsed`
- `bitsCount`
- `selectedAngle`
- `remainingAngles`
- `totalAnglesSelectedFirst`
- `TangentsDB`
- `remainingBits`
- `insufficientBits`

### 6.3 Naturalness Gate

`WORKFLOW_NATURALNESS_GATE_ENABLED=1` enables the naturalness gate. It does not change prompt text.

The gate has two parts:

- angle relevance scoring after `genAngles`;
- comment plausibility validation during stego candidate validation.

The settled default mode is `WORKFLOW_NATURALNESS_GATE_MODE=middle`. Middle mode hard-rejects only angles with no post/comment topic overlap. It records source quote fragments as warnings, but does not reject them when the angle is still anchored to the post topic.

Strict mode is available for experiments, but should not be the default because the 100-sample clean batch showed that strict filtering improved KL/JSD slightly while dropping successful samples from `98/100` to `86/100`.

Full experiment notes are in `docs/development/naturalness-gate-v1.md`.

## 7. Sender Audit

After augmentation, `StegoPipeline.encode()` builds `sender_audit`.

Important fields:

- dictionary hashes and counts;
- angles hash and count;
- selected URL hashes;
- selected angle index;
- compression metadata, including `compressed` and `compressed_hash`;
- `selection_signature`;
- `comment_bits`;
- `angle_bits`;
- encoding settings;
- payload transform;
- payload carrier: `selection_channel`;
- raw and embedded byte counts;
- LLM timing records.

This audit is not required for the pure codec, but it helps the receiver:

- detect context drift;
- use the exact compressed bitstring;
- use the expected angle index if semantic decode drifts;
- know which payload transform to reverse.

## 8. Visible Stego Text Generation

Once the selected angle is known, the sender needs visible text that the decoder will map back to that selected angle.

There are two broad generation paths.

### 8.1 Extractive Mode

`_encode_extractive_zero_kld()` is used when `WORKFLOW_STEGO_GENERATION_MODE` is `extractive_zero_kld` or `hybrid_extract`.

It tries to use existing visible text from comments/selftext when that text already matches the selected angle. This avoids generating new prose.

### 8.2 LLM Generation Mode

The normal path:

1. `_build_samples()` takes the selected angle plus additional candidates from `totalAnglesSelectedFirst`.
2. It calls `BackendAPIAdapter.needle_finder_batch()` to match each angle `source_quote` against `search_results`.
3. It builds sample dicts with `best_match`.
4. `_generate_stego_texts()` builds a prompt from `stego_encode_prompts_for_style()`.
5. It calls `LLMAdapter.call_llm()` through `resolve_workflow_llm_provider_and_model()`.
6. It expects exactly three non-empty candidate strings in JSON form.
7. It parses those candidate strings and returns only model output (no post-generation
   templated/synthetic candidate replies).

The generated text must be ordinary visible text. New sender code must not use zero-width, invisible, control-format, homoglyph, or non-rendering Unicode carriers. Post-generation code must not create, append, splice, or template a visible candidate from an angle, tangent, source quote, payload, or decoder result.

## 9. Sender Candidate Validation

`_evaluate_candidate_groups()` validates each generated candidate:

1. For each candidate text, call `DecodePipeline.decode()` in strict mode.
2. If strict decode fails, try relaxed mode.
3. Compare decoded index to `selected_angle["idx"]`.
4. Run `_contextuality_gate()` to reject generic or off-context text.
5. Record rejection reasons such as:
   - `generic_editorial_tone`
   - `no_context_overlap`
   - `unsupported_topic_drift`
   - `weak_selected_angle_grounding`
   - `adjacent_angle_mismatch`
   - `weak_decoder_mode`
   - `decode_mismatch`
6. Sort evaluations to prefer accepted/context-safe matches.
7. Accept only strict exact matches that pass contextuality.

If validation fails, the retry loop may:

- retry with a different prompt style;
- context-sharpen promising candidates;
- return a structured failure after retries are exhausted.

On success, `StegoPipeline.encode()` returns:

- `stego_text`
- original `post`
- `selected_angle`
- `angle_index`
- `succeeded`
- `retry_count`
- `tag`
- `sender_audit`
- `breakdown`
- `embedding`
- `encoded_samples`
- validation metadata

`process_post()` persists the n8n-shaped artifact to `output-results` only when `succeeded` is true and `stego_text` is non-empty.

## 10. Decode Pipeline: Visible Text To Angle Index

`DecodePipeline.decode(stego_text, angles, few_shots=None, strict_mode=False)` maps visible stego text to an angle index.

Steps:

1. Validate that `angles` is non-empty.
2. Call `BackendAPIAdapter.semantic_search(text=stego_text, objects=angles, n=semantic_pool_n)`.
3. Map returned objects back to canonical angle indices by angle signature.
4. Rerank candidates with semantic score plus lexical overlap.
5. Build a decode prompt from `get_prompts().stego_decode`.
6. Call `LLMAdapter.call_llm()`.
7. Parse the response, preferring:
   - JSON `"idx": N`
   - labeled `idx: N`
   - final-line digits
   - last/first allowed digit fallback
   - rank fallback
8. In strict mode, only structured/final-line parse modes are accepted.
9. In relaxed mode, if parsing fails, fall back to top semantic candidate.

The output is a 0-based angle index or `None`.

## 11. Receiver: Locate Sender Comment

`ReceiverPipeline.run(post, sender_user_id, ...)` starts with:

```python
located = locate_sender_stego_comment(post, sender_user_id)
```

`locate_sender_stego_comment()`:

1. Flattens the comment tree.
2. Keeps comments where `author == sender_user_id` or `author_id == sender_user_id`.
3. Requires non-empty `body`.
4. Returns the first match.
5. Logs a warning if there are multiple matches.

The receiver uses:

- `located["body"]` as `stego_text`;
- `located["id"]` as the comment id to remove.

## 12. Receiver: Rebuild Pre-Sender Context

The receiver must rebuild the context that existed before the sender comment was added.

`build_pre_sender_post(post, sender_comment_id)`:

1. Clones the post shallowly.
2. Recursively removes the sender comment subtree by id.
3. Returns the pre-sender post.

If the id cannot be found, it raises.

## 13. Receiver: Rebuild Data/Research/Angles

`ReceiverPipeline.rebuild_context(pre_sender_post, ...)` runs:

1. `DataLoadPipeline.preview_post()`
2. `ResearchPipeline.preview_post()`
3. `GenAnglesPipeline.preview_post()`
4. `build_dictionary_report(rebuilt)`

It returns:

- rebuilt post;
- summary hashes and counts;
- stage reports.

The summary includes:

- selftext hash and length;
- search results hash and count;
- angles hash and count;
- dictionary id/hash/count;
- dictionary source/capacity metadata.

## 14. Receiver: Context Drift Check

If sender audit is present, `_context_drift_mismatches()` compares:

- `dictionary_hash`
- `dictionary_count`
- `angles_hash`
- `angles_count`
- `selected_urls_hash`

If mismatches exist and `fail_on_context_drift=True`, `ReceiverPipeline.run()` returns a failed result at stage `context_drift` before decoding payload.

## 15. Receiver: Decode Payload

`ReceiverPipeline.decode_payload()` performs:

1. Flatten rebuilt angles into `tangents_db`.
2. Decode visible `stego_text` to `decoded_idx` with `DecodePipeline.decode()`.
3. Canonicalize duplicate angle signatures if needed.
4. If sender audit has `selected_angle_index` and semantic decode differs:
   - strict mode raises;
   - relaxed mode uses the sender audit index as authoritative.
5. Rebuild dictionary from rebuilt post.
6. Recover the protected payload.
7. Reverse payload transform with `unprotect_payload()`.

Payload recovery has two modes.

### 15.1 Recovery With Full Compressed Bitstring

If `compressed_full` is provided or found in `sender_audit["compression"]["compressed"]`, the receiver calls:

```python
recover_payload_with_compressed_full(
    compressed_full,
    dictionary,
    pre_sender_post,
    nested_angles,
    decoded_angle_index,
)
```

It:

1. Recomputes comment bit width `lc`.
2. Recomputes angle bit width `la`.
3. Extracts `comment_bits = compressed_full[:lc]`.
4. Extracts `angle_bits = compressed_full[lc:lc+la]`.
5. Checks that `angle_bits` decode to the authoritative angle index.
6. Decompresses the full compressed bitstring.

This is the most direct recovery path.

### 15.2 Brute-Force Comment Bits

If no full compressed bitstring is available, the receiver calls:

```python
recover_payload_bruteforce_comment_bits(...)
```

It:

1. Recomputes `lc` and `la`.
2. Enumerates every possible comment-bit prefix: `2 ** lc`.
3. Enumerates every angle-bit alias that maps to the decoded angle index.
4. Builds possible compressed prefixes.
5. Tries padding up to `max_padding_bits`.
6. Decompresses candidates.
7. Re-compresses candidates and requires the compressed output to start with the same prefix.
8. Returns the shortest valid candidate.

This works because the receiver knows the angle index from visible text and can brute-force the small comment-selection prefix.

## 16. Receiver Output

On success, `ReceiverPipeline.run()` returns:

- `succeeded: true`
- `post_id`
- `payload`
- `located_comment`
- `rebuild_summary`
- `decoded_angle_index`
- `recovery_meta`
- `rebuild_reports`
- `context_drift`

`recovery_meta` includes:

- `comment_bits`
- `angle_bits`
- `lc`
- `la`
- compression method when known
- `payload_carrier: selection_channel`
- `payload_transform`
- embedded and recovered byte counts

## 17. Running Example 1: Pure Codec Round Trip

This example does not call the live LLM or backend services. It exercises the shared codec contract.

Run from repo root:

```powershell
$env:PYTHONPATH='src'
@'
import json
from workflows.utils.stego_codec import (
    augment_post,
    build_dictionary,
    recover_payload_with_compressed_full,
    recover_payload_bruteforce_comment_bits,
)
from workflows.pipelines.receiver import nested_angles_from_post

post = {
    "id": "example-1",
    "title": "City council park funding",
    "selftext": "The council debated park lighting and sidewalk repairs.",
    "url": "https://example.com/parks",
    "comments": [
        {"id": "c1", "author": "alice", "body": "The lighting near the east path needs priority.", "replies": []},
        {"id": "c2", "author": "bob", "body": "Sidewalk repairs should come before new banners.", "replies": []},
    ],
    "angles": [
        {"source_quote": "park lighting", "tangent": "Discuss safety concerns around park lighting.", "category": "Public Safety"},
        {"source_quote": "sidewalk repairs", "tangent": "Discuss maintenance tradeoffs for sidewalk repairs.", "category": "Infrastructure"},
        {"source_quote": "new banners", "tangent": "Discuss cosmetic spending versus core maintenance.", "category": "Budget"},
    ],
}

payload = "MEET_AT_9"
aug = augment_post(payload, post)
comp = aug["compression"]["compressed"]
dictionary = build_dictionary(post)
nested = nested_angles_from_post(post)
angle_idx = aug["angleEmbedding"]["selectedAngle"]["idx"]

full = recover_payload_with_compressed_full(comp, dictionary, post, nested, angle_idx)
brute = recover_payload_bruteforce_comment_bits(
    dictionary,
    post,
    nested,
    angle_idx,
    max_padding_bits=256,
    compressed_full=comp,
)

print(json.dumps({
    "payload": payload,
    "dictionary_entries": len(dictionary),
    "compression_method": aug["compression"]["method"],
    "compressed_prefix": comp[:24],
    "comment_bits": aug["commentBits"],
    "angle_bits": aug["angleBits"],
    "picked_context_type": aug["commentEmbedding"]["targetType"],
    "picked_comment_ids": [c["id"] for c in aug["commentEmbedding"]["pickedCommentChain"]],
    "selected_angle_index": angle_idx,
    "selected_angle_category": aug["angleEmbedding"]["selectedAngle"].get("category"),
    "recover_with_full": full[0] if full else None,
    "recover_with_bruteforce": brute[0] if brute else None,
}, indent=2))
'@ | uv run python -
```

Observed output:

```json
{
  "payload": "MEET_AT_9",
  "dictionary_entries": 3,
  "compression_method": "standard",
  "compressed_prefix": "001001101010001010100010",
  "comment_bits": "00",
  "angle_bits": "10",
  "picked_context_type": "post",
  "picked_comment_ids": [],
  "selected_angle_index": 2,
  "selected_angle_category": "Budget",
  "recover_with_full": "MEET_AT_9",
  "recover_with_bruteforce": "MEET_AT_9"
}
```

What happened:

1. The payload became a standard compressed bitstream.
2. The first two bits selected post-level context.
3. The next two bits selected angle index `2`.
4. Recovery used the same dictionary, pre-sender post, nested angles, and decoded angle index.
5. Both recovery methods returned the original payload.

## 18. Running Example 2: Receiver Recovery With Mocked Rebuild/Decode

This example mirrors the receiver pipeline without calling live data-load, research, angle generation, semantic search, or an LLM. Those steps are mocked so the payload extraction path is isolated.

Run from repo root:

```powershell
$env:PYTHONPATH='src'
@'
import json
from workflows.pipelines.receiver import ReceiverPipeline
from workflows.utils.stego_codec import augment_post

pre_sender = {
    "id": "example-2",
    "title": "City council park funding",
    "selftext": "The council debated park lighting and sidewalk repairs.",
    "url": "https://example.com/parks",
    "comments": [
        {"id": "c1", "author": "alice", "body": "The lighting near the east path needs priority.", "replies": []},
        {"id": "c2", "author": "bob", "body": "Sidewalk repairs should come before new banners.", "replies": []},
    ],
    "angles": [
        {"source_quote": "park lighting", "tangent": "Discuss safety concerns around park lighting.", "category": "Public Safety"},
        {"source_quote": "sidewalk repairs", "tangent": "Discuss maintenance tradeoffs for sidewalk repairs.", "category": "Infrastructure"},
        {"source_quote": "new banners", "tangent": "Discuss cosmetic spending versus core maintenance.", "category": "Budget"},
    ],
}

encoded = augment_post("PAYLOAD-42", pre_sender)
angle_idx = encoded["angleEmbedding"]["selectedAngle"]["idx"]

full_post = dict(pre_sender)
full_post["sender_audit"] = {
    "selected_angle_index": angle_idx,
    "payload_transform": "plain",
    "compression": {"compressed": encoded["compression"]["compressed"]},
}
full_post["comments"] = [
    *pre_sender["comments"],
    {
        "id": "stego_c",
        "author": "sender1",
        "body": "The lighting detail seems tied to park safety, not just aesthetics.",
        "replies": [],
    },
]

rp = ReceiverPipeline()
rp.data_load.preview_post = lambda post, use_cache=True: {
    "post": {**post, "selftext": pre_sender["selftext"]},
    "report": {"fetch_success": True},
}
rp.research.preview_post = lambda post, force=True, **kwargs: {
    "post": {**post, "search_results": []},
    "report": {},
}
rp.gen_angles.preview_post = lambda post, allow_fallback=False: {
    "post": {**post, "angles": pre_sender["angles"], "options_count": len(pre_sender["angles"])},
    "report": {},
}
rp.decode.decode = lambda **kwargs: angle_idx

out = rp.run(
    full_post,
    "sender1",
    use_fetch_cache=False,
    use_terms_cache=False,
    persist_terms_cache=False,
    use_fetch_cache_research=False,
)

print(json.dumps({
    "receiver_succeeded": out["succeeded"],
    "located_comment_id": out["located_comment"]["id"],
    "decoded_angle_index": out["decoded_angle_index"],
    "recovered_payload": out["payload"],
    "payload_carrier": out["recovery_meta"]["payload_carrier"],
    "recovered_comment_bits": out["recovery_meta"]["comment_bits"],
    "recovered_angle_bits": out["recovery_meta"]["angle_bits"],
}, indent=2))
'@ | uv run python -
```

Observed output:

```json
{
  "receiver_succeeded": true,
  "located_comment_id": "stego_c",
  "decoded_angle_index": 2,
  "recovered_payload": "PAYLOAD-42",
  "payload_carrier": "selection_channel",
  "recovered_comment_bits": "00",
  "recovered_angle_bits": "10"
}
```

What happened:

1. The receiver found the comment authored by `sender1`.
2. It removed comment `stego_c` to rebuild the pre-sender post.
3. The mocked rebuild returned the same angles and context.
4. The mocked decoder returned the angle index selected by the sender.
5. The receiver used `sender_audit.compression.compressed` to recover the protected payload.
6. The `plain` transform returned `PAYLOAD-42`.

## 19. Real API Shape

Sender:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5001/api/v1/workflows/stego `
  -ContentType application/json `
  -Body '{"post_id":"POST_ID","payload":"PAYLOAD-42","tag":"demo"}'
```

Receiver:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5001/api/v1/workflows/receiver `
  -ContentType application/json `
  -Body '{"post":{...},"sender_user_id":"sender1","fail_on_context_drift":true}'
```

Live sender-to-receiver simulation:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5001/api/v1/workflows/stego-receiver-live `
  -ContentType application/json `
  -Body '{"sender_user_id":"sender1","payload":"PAYLOAD-42","tag":"demo","max_post_attempts":5}'
```

## 20. Failure Points To Check

- No available post: `posts_list()` returns no filenames.
- Missing payload: no explicit payload and no default in workflow JSON.
- Missing angles: sender cannot encode without `post["angles"]`.
- Inefficient dictionary: sender falls back to standard compression.
- Padding used: compressed payload ran out before comment or angle selection width.
- No samples: selected angle/sample construction failed.
- Candidate generation failed: LLM output was not valid JSON with exactly three strings.
- Candidate validation failed: generated text does not decode to selected angle or is not context-safe.
- Receiver cannot locate sender comment.
- Receiver cannot remove sender comment by id.
- Receiver data-load/research/gen-angles rebuild fails.
- Context drift between sender audit and receiver rebuild.
- Decode pipeline cannot map visible text to an angle index.
- Compressed bitstring does not match decoded angle index.
- Payload transform cannot be reversed because secret/config differs.
