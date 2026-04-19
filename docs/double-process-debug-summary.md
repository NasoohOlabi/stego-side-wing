# Double-Process Debug Summary

## Scope

This note summarizes the debugging and live validation work done on the
`/api/v1/workflows/double-process-new-post` path for post `1lrjlzj`,
specifically around Google/Gemini failures in the `gen_angles` stage.

This document only covers this debugging slice. The worktree contains other
unrelated changes that are not described here.

## Problem Summary

The workflow was failing at `gen_angles` with:

- `RuntimeError: GenAngles returned no result for post 1lrjlzj`

The underlying transport failures were Google/Gemini request failures such as:

- `httpx.RemoteProtocolError: Server disconnected without sending a response`
- `requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))`
- TLS handshake timeouts on the `google.genai` client path

## Main Code Changes

### 1. Gemini retry, timeout, key rotation, and REST fallback

File: `src/workflows/adapters/llm.py`

Changes:

- Added broader retryable transport-error detection for Gemini.
- Added retry/rotation behavior for transport failures, not just auth/quota HTTP
  errors.
- Added a configurable Google request timeout via
  `GOOGLE_AI_REQUEST_TIMEOUT_SEC`.
- Added fallback from the `google.genai` client path to direct REST
  `generateContent` when retryable transport errors occur.
- Added REST response parsing that prefers non-thinking parts and falls back to
  the remaining text parts if necessary.
- Added a safe fallback for tests that instantiate `LLMAdapter` with
  `__new__()` and therefore do not populate all runtime fields.
- Added a retry path for Gemini requests without `system_message` after
  transport failures.

### 2. Workflow angles transport splitting

File: `src/content_acquisition/angles/angle_runner.py`

Changes:

- Added workflow-side transport-error detection using
  `LLMAdapter.last_call_metadata`.
- Added recursive splitting for workflow-Google angle batches when the failure
  looks like a transport problem.
- Added structured logging for workflow transport splits:
  `angles_workflow_transport_split`.
- Disabled Gemini `system_message` usage on the workflow angles path and on the
  JSON repair path.
- Lowered the minimum recursive split threshold:
  - from the old effective `4000` chars
  - to `500`
  - then to `128` after live validation showed the failing prompt was still too
    small to split

### 3. Angles prompt cleanup

Files:

- `src/content_acquisition/angles/systemPrompt.txt`
- `src/workflows/utils/angles_llm_config.py`

Changes:

- Replaced the previous prompt text with a clean JSON-only instruction set.
- Removed unsafe/non-machine-readable text from the checked-in prompt.
- Trimmed the trailing newline when loading the prompt to reduce prompt-shape
  sensitivity.

### 4. Config support

File: `src/infrastructure/config.py`

Changes:

- Added `DEFAULT_GOOGLE_AI_REQUEST_TIMEOUT_SEC = 180`
- Added `get_google_ai_request_timeout_seconds()`

## Tests Added or Updated

### Gemini adapter tests

File: `src/tests/test_llm_adapter_retries.py`

Coverage added:

- retry on `RemoteProtocolError`
- key rotation after transport failures
- retry without system message
- system-message fallback plus key rotation
- configured timeout passed into the Gemini client
- REST fallback after `google.genai` transport failure

### Workflow angles tests

File: `src/tests/test_angles_service_workflow_backend.py`

Coverage added:

- workflow-Google transport split recovery for a large prompt
- workflow-Google transport split recovery for a short prompt
- workflow-Google transport split recovery for a very short prompt
- clean/machine-readable angles system prompt assertions

### Legacy LM Studio retry test isolation

File: `src/tests/test_angle_runner_llm_retries.py`

Change:

- Pinned the fixture to the LM Studio path directly so those tests do not
  accidentally hit the Google workflow path because of local env/config state.

## Validation Performed

Targeted checks that passed during this work:

- `uv run pytest -q src/tests/test_angle_runner_llm_retries.py src/tests/test_angles_service_workflow_backend.py src/tests/test_llm_adapter_retries.py src/tests/test_llm_redacted_thinking.py src/tests/test_api_v1_double_process_new_post.py src/tests/test_workflow_runner.py src/tests/test_pipeline_gen_angles.py`
- `uv run pyright src/content_acquisition/angles/angle_runner.py src/tests/test_angle_runner_llm_retries.py src/tests/test_angles_service_workflow_backend.py src/workflows/adapters/llm.py src/infrastructure/config.py src/tests/test_llm_adapter_retries.py`
- `uv run pyright`

Additional notes:

- A full `uv run pytest -q` was attempted earlier in the session and was not
  clean in this worktree because of unrelated env/config failures outside this
  debugging slice. Those were not part of the angle/Gemini fixes summarized
  here.

## Important Findings

### The user-supplied Google keys were not the core problem

- Both supplied Google keys were directly probed against the Google
  `generateContent` endpoint and returned `200 OK`.
- One live issue was that an API process was still seeing an extra key from
  local env state, so later reruns explicitly pinned the server process to the
  two supplied keys only.

### The original failure mode changed over time

The debug sequence moved through these stages:

1. Original behavior: transport failure during `gen_angles`, followed by generic
   `GenAngles returned no result`.
2. Timeout fix: the same failure terminated cleanly instead of hanging
   indefinitely.
3. REST fallback fix: the `google.genai` TLS/handshake path was avoided when it
   failed, but direct REST could still be closed by Google.
4. First split fix: large prompts could split, but the real failing prompt was
   still too short to qualify.
5. Final split-threshold fix: the previously failing short prompt now splits
   into smaller follow-up prompts and the workflow continues instead of dying at
   that exact point.

### The current blocker changed

The main blocker is no longer "the two Google keys are invalid" and no longer
"the workflow always dies at the first short transport failure."

The current blocker is:

- Google transport instability on some angle prompts still exists, but the code
  now recovers by splitting and continuing.
- The sync caller can time out before the server finishes the longer recovery
  path.

## Current Outcome

As of the latest check on `2026-04-19`:

- The API is healthy on `127.0.0.1:5001`.
- The latest fully completed failure report is still:
  `datasets/double_process_validation/reports/1lrjlzj_1776580670690.json`
- A newer run started later and is still active in the server log.
- That in-flight run shows the short failing prompt being split and processed in
  follow-up sub-requests instead of failing immediately.

## Recommended Next Steps

1. Add an async/report-polling or streaming-first path for
   `double-process-new-post` so the client does not time out while the server
   keeps working.
2. Improve final report persistence for long-running sync requests so a terminal
   status is still recorded even when the caller disconnects.
3. Consider switching the workflow Google model if this specific model remains
   transport-unstable for angle extraction.
4. If Google remains unreliable, route this stage to LM Studio or another
   backend for comparison.
