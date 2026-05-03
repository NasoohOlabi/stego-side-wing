# Log Navigation And Frontend Tips

This guide explains how to read `logs/api.jsonl` quickly and how a frontend log viewer can render the data so failures are easy to spot.

## What The Log Already Gives Us

Each line in `logs/api.jsonl` is a standalone JSON object. In practice, the most useful fields are:

- `timestamp`
- `level`
- `component`
- `message`
- `trace_id`
- `run_id`
- `post_id` when the pipeline includes it in the message
- `extra.*` for LLM transport details such as `attempt`, `error_kind`, `http_status`, `response_snippet`, `prompt_hash`, and `system_prompt_hash`

The run lifecycle starts in [workflow_run_tracker.py](/D:/Master/code/stego-side-wing/src/services/workflow_run_tracker.py:30), which logs:

- `workflow_run_register`
- `workflow_run_end`

The LLM retry lifecycle is logged in [llm.py](/D:/Master/code/stego-side-wing/src/workflows/adapters/llm.py:621), which emits:

- `llm_request_begin`
- `llm_request_retry`
- `llm_request_failed`

The stego encode loop is logged in [stego.py](/D:/Master/code/stego-side-wing/src/workflows/pipelines/stego.py:649), which emits:

- attempt start
- candidate generation summary
- validation failure details
- final failure reason when retries are exhausted

## How I Navigate The Log

When a post fails, I do not read the whole file top to bottom. I narrow it in layers.

### 1. Start From `run_id`

The fastest way to reconstruct a single workflow is:

1. Find `workflow_run_register`.
2. Keep only lines with the same `run_id`.
3. Build a timeline from earliest to latest.

That gives the outer frame of the request before diving into one post.

## 2. Narrow From Run To Post

Inside a run, I filter for the target `post_id`. That isolates the pipeline attempts for one post and usually shows:

- when the post entered the stego pipeline
- how many retries happened
- whether failure happened during generation, validation, or final writeback

For stego failures, the most valuable lines are the ones shaped like:

- `post_id=... attempt=1/5 selected_idx=...`
- `post_id=... attempt=... type=ConnectionError`
- `post_id=... attempt=... failed selected_idx=... decoded_indices=[...]`
- `post_id=... reason=...`

## 3. Separate Transport Errors From Prompt/Output Errors

I then classify each error into one of these buckets:

- Transport failure: connection reset, remote disconnect, timeout, 5xx
- Contract failure: the model replied, but format was wrong
- Validation failure: the model replied with valid format, but the decoded result did not match the selected angle

This distinction matters because the fix lives in different places:

- transport errors point to the provider or network
- contract failures point to model behavior or response sanitizing
- validation failures point to prompt quality, model suitability, or stego robustness

## 4. Use `component` As A Lane

I mentally group the log into lanes:

- `services.workflow_run_tracker`: run start/end
- `app.routes.api_v1.workflow_streaming`: request/stream boundaries
- `LLMAdapter`: provider calls, retries, transport diagnostics
- `StegoPipeline`: per-post encode attempts and validation
- `DecodePipeline`: cross-validation and index resolution

Reading by component is much faster than reading by raw line order alone.

## 5. Look For Repeated Hashes Before Blaming The Prompt

For LLM calls, `prompt_hash` and `system_prompt_hash` are very useful.

If the same prompt hash fails repeatedly with connection resets, the issue is likely transport, not the prompt.

If the same prompt hash repeatedly returns malformed content, the issue is likely:

- model formatting behavior
- server-side chat template behavior
- a prompt contract that is too weak for that model

## 6. Watch The End Of The Retry Loop

The most important lines are usually near the end of a failed attempt chain:

- final `llm_request_failed`
- `post_id=... attempt=... type=...`
- stego validation failure
- final `succeeded=False`

These tell you the real terminal state, not just intermediate noise.

## How I Read The Example Failure Pattern

For the `run_id=b758744a84dd43f98525dc6ad7cf9c92` and `post_id=1npb37u` case, the log showed three distinct failure classes:

1. LM Studio transport failures
   `ConnectionResetError(10054)` and `RemoteDisconnected`

2. One contract failure
   The model returned JSON wrapped in LM Studio control tokens:
   `<|channel|>final <|constrain|>json<|message|>...`

3. Stego validation failures
   The generated candidate texts decoded to indices different from `selected_idx`

That is why the correct diagnosis was not just "bad prompt".

## Frontend Rendering Tips

If the frontend server wants to make this log useful, the UI should not show raw JSONL as a flat wall of text. It should render the logs as grouped diagnostic objects.

## Recommended Views

### 1. Run Timeline View

Primary grouping key:

- `run_id`

Show each run as a collapsible card with:

- start time
- duration
- command
- status
- number of posts processed
- number of warnings and errors

Inside the card, show a chronological timeline.

### 2. Post Failure View

Secondary grouping key:

- `post_id`

Within a run, render each post as a sub-card showing:

- current status
- retry count
- selected angle index
- final failure type
- validation mismatch summary

This is the fastest way to answer "why did this post fail?"

### 3. Component Lanes

Split the timeline into visual lanes by `component`:

- Run tracker
- API stream
- LLM adapter
- Stego pipeline
- Decode pipeline

This makes causal relationships much easier to see.

## Recommended Highlight Rules

Coloring should reflect failure class, not only log level.

- Red: terminal errors
- Orange: retries and validation mismatches
- Yellow: warnings that may still recover
- Blue: run boundaries
- Gray: verbose prompt or payload logs

Use icons or badges for:

- `ConnectionError`
- `HTTPError`
- `RuntimeError`
- `validation failed`
- `retryable=true`

## Recommended Derived Signals

The frontend should compute a few synthetic fields from the raw logs.

### Per Run

- `run_status`: success, partial failure, failed, in progress
- `error_count`
- `warning_count`
- `posts_failed`
- `posts_succeeded`
- `top_error_kind`

### Per Post

- `attempt_count`
- `final_error_kind`
- `selected_idx`
- `decoded_indices_last`
- `llm_retry_count`
- `had_transport_error`
- `had_contract_error`
- `had_validation_error`

### Per LLM Call

- `provider`
- `model`
- `attempt / attempts_max`
- `elapsed_ms`
- `error_kind`
- `http_status`
- `retryable`

## Recommended Collapsed Summary Rows

Instead of showing every raw line first, the UI should lead with a summary like:

`Post 1npb37u failed after 5 attempts: 2 connection errors, 1 invalid LM output, final validation mismatch`

Then allow expansion into raw events.

This saves a lot of time.

## Recommended Search And Filter UX

At minimum, support filters for:

- `run_id`
- `trace_id`
- `post_id`
- `component`
- `level`
- `error_kind`
- `provider`
- `model`

Good quick filters:

- `Only errors`
- `Only retries`
- `Only this run`
- `Only this post`
- `Only LLMAdapter`
- `Only terminal failures`

## Recommended Field Extraction

Some important identifiers live inside `message` text rather than top-level fields, especially `post_id` and attempt counters in stego logs. The frontend should extract and normalize:

- `post_id`
- `attempt_current`
- `attempt_max`
- `selected_idx`
- `decoded_indices`

Parsing these once on the server side will make the frontend much cleaner.

## Useful Sorting Rules

Default sort should be:

1. errors first
2. warnings next
3. newest runs first

Inside a run:

1. timeline order by `timestamp`
2. sticky summary at top
3. terminal failure lines pinned or repeated in a summary panel

## What To Hide By Default

Some logs are valuable but noisy:

- full prompts
- full raw LLM responses
- repeated `llm_request_begin`
- large stack traces

Do not remove them, but collapse them behind toggles:

- `Show prompt`
- `Show raw response`
- `Show stack trace`
- `Show retries`

## What To Surface Immediately

The frontend should surface these without expansion:

- terminal error message
- provider and model
- retry count
- whether the failure was transport, contract, or validation
- the last `response_snippet` for LLM failures
- selected angle and last decoded indices for stego validation failures

## Suggested Server-Side Processing Pipeline

If the frontend server preprocesses `api.jsonl`, this is a good order:

1. Parse JSONL into records.
2. Normalize top-level keys and `extra.*`.
3. Extract `post_id`, attempt counters, and selected/decode indices from `message`.
4. Group by `run_id`.
5. Group posts inside each run.
6. Compute per-run and per-post summaries.
7. Mark terminal events.
8. Emit both raw events and summarized view models to the UI.

## Practical Rule Of Thumb

When scanning logs quickly, ask these three questions in order:

1. Did the run start and end cleanly?
2. Did the post fail because the provider never answered, answered in the wrong format, or answered with the wrong semantic result?
3. Which final line proves that conclusion?

If the frontend makes those three answers obvious, log triage becomes much faster.
