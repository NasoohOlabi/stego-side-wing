# Double-Process Live Findings

## Scope

This is the evidence-oriented companion to
`docs/double-process-debug-summary.md`.

It records the live reruns, logs, reports, and current status observed while
debugging `double-process-new-post` for post `1lrjlzj`.

API keys are intentionally not included here.

## Key Findings

### 1. Initial confirmed failure

The workflow repeatedly failed with a final report containing:

- `succeeded: false`
- `comparison_completed: false`
- `error_message: "GenAngles returned no result for post 1lrjlzj"`

The underlying cause in logs was a Gemini transport failure during `gen_angles`,
not a business-logic validation error.

### 2. The supplied Google keys were valid

Both user-supplied Google keys were directly tested against the Google
`generateContent` endpoint and returned `200 OK`.

Conclusion:

- key validity was not the root cause
- the remaining problem was transport/model/runtime behavior

### 3. A third key could leak in from local env state

One server process still saw an extra Google key from local env loading.

After that was found, later reruns explicitly launched the API with only the
two supplied keys in the process environment so the fallback order was known and
controlled.

### 4. The failure evolved across multiple reruns

#### Timeout-fix rerun

Artifacts:

- `tmp_api_timeoutfix_stderr.log`
- `tmp_double_process_timeoutfix_response.txt`
- `datasets/double_process_validation/reports/1lrjlzj_1776577896957.json`

Outcome:

- the workflow still failed
- but it now terminated cleanly with a final report instead of disappearing
- the previous hanging behavior was improved

#### REST-fallback rerun

Artifacts:

- `tmp_api_restfallback_stderr.log`
- `tmp_double_process_restfallback_response.txt`
- `datasets/double_process_validation/reports/1lrjlzj_1776578701109.json`

Outcome:

- the workflow still failed
- the `google.genai` handshake/transport path now fell back to direct REST
- Google could still close the REST connection with
  `RemoteDisconnected('Remote end closed connection without response')`

#### First short-split rerun

Artifacts:

- `tmp_api_smallsplit_stderr.log`
- `tmp_double_process_smallsplit_response.txt`
- `datasets/double_process_validation/reports/1lrjlzj_1776580670690.json`

Outcome:

- the workflow still failed
- the new workflow split logic was present
- but the failing prompt was only about `1619` chars, and the split floor was
  still too high, so it did not split in that run

#### Final short-split rerun

Artifacts:

- `tmp_api_smallsplit2_stderr.log`
- `tmp_double_process_smallsplit2_response.txt`
- `prompts.log`

Outcome:

- the client-side sync request timed out after 30 minutes
- the server itself stayed healthy
- the previously failing short Gemini prompt now split into follow-up requests
- the run continued instead of terminating at the old failure point

## Concrete Evidence

### Latest completed terminal report

Latest completed report for `1lrjlzj`:

- `datasets/double_process_validation/reports/1lrjlzj_1776580670690.json`

Contents:

```json
{
  "mode": "double_process_new_post",
  "succeeded": false,
  "comparison_completed": false,
  "post_id": "1lrjlzj",
  "source_file": "1lrjlzj.json",
  "error_type": "RuntimeError",
  "error_message": "GenAngles returned no result for post 1lrjlzj"
}
```

### Evidence that the short prompt eventually split

In `tmp_api_smallsplit2_stderr.log`, the previously failing prompt hash
`ff56ae...` exhausted retries and then logged:

- `angles_workflow_transport_split`
- `depth: 0`
- `sub_batches: 3`

This is the key sign that the latest threshold change actually affected the
real live failure.

In `prompts.log`, the follow-up prompt sizes after that split included:

- `1419`
- `1432`
- `1460`
- `1489`
- `1502`
- `1541`
- `1608`
- `1734`
- `1793`
- `2247`

That is materially different from the earlier runs where the short failing
prompt stayed a single request and immediately died.

## Current Live Status

At the latest check on `2026-04-19`:

- `/api/v1/health` returned `200 OK`
- the active claim file still existed:
  `datasets/double_process_validation/active_post_claim.json`
- the latest active run was:
  - `run_id: a6648724bc6b462391183c937965b563`
  - `trace_id: d47af395-de08-4afa-9abe-ced0311354c2`
- no newer terminal `1lrjlzj` report had been written yet
- the live log still showed new LLM request starts after the sync client timed
  out

This means:

- the server is still working
- the synchronous caller timed out before the server completed
- the workflow has moved past the old immediate-failure point

## Net Assessment

### Fixed or improved

- transport failures now retry and rotate across Google keys
- Gemini can fall back from `google.genai` to direct REST
- workflow angle batches can recursively split on transport failures
- very short failing angle prompts now qualify for split recovery
- the server no longer appears to silently vanish at the original failure point

### Still unresolved

- Google transport instability still happens on some angle prompts
- sync callers can time out before the recovery path finishes
- the newest long-running run has not yet produced a terminal success/failure
  report

## Suggested Next Move

The next highest-value engineering step is to stop relying on a long synchronous
HTTP request for this workflow.

Recommended options:

1. return immediately with a run id and poll for status
2. stream progress and persist terminal state independently of the client
3. keep the current sync route only for short runs and route longer runs to a
   background execution model
