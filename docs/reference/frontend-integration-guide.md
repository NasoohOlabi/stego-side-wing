# Frontend Integration Guide

This file is the frontend handoff for the current backend at `/api/v1`.

For full backend detail, see [api-spec.md](api-spec.md).

## 1. Base Contract

- Base URL: `/api/v1`
- Auth: none right now
- Success shape:

```json
{
  "ok": true,
  "data": {}
}
```

- Error shape:

```json
{
  "ok": false,
  "error": "Human-readable message",
  "details": {}
}
```

Frontend rule:

- Always branch on `ok`.
- Show `error` to users.
- Treat `details` as debug/operator detail.

## 2. Long-Running Workflow Behavior

Most workflow endpoints stream by default with SSE.

- Default response type: `text/event-stream`
- Disable streaming with `?stream=0` or JSON body `{ "stream": false }`
- Event names:
  - `status`
  - `progress`
  - `log`
  - `result`
  - `error`
  - `done`

Frontend rule:

- Use SSE for operator screens.
- Use non-stream JSON mode for simple forms or scripted actions.
- Treat `result` as the final useful payload.
- Treat `done` as transport completion, not the business payload.

## 3. Recommended UI Structure

### Dashboard

Use these endpoints:

- `GET /health`
- `GET /state/paths`
- `GET /state/logs`
- `GET /logging/tags`
- `GET /state/recent-updates`
- `GET /workflows/runs`

Show:

- API health
- repo root
- important paths
- active runs count
- recent git updates
- log file size

### Workflow Runner

Use:

- `GET /workflows/pipelines`
- `POST /workflows/run`
- or dedicated workflow endpoints under `/workflows/*`

Show:

- command picker
- JSON/body editor per command
- live progress timeline
- final result panel
- error panel

Prefer dedicated forms for:

- `data-load`
- `research`
- `gen-angles`
- `stego`
- `receiver`
- `validate-post`
- `double-process-new-post`
- `prep-until-google-quota-then-stego`

### Artifact Explorer

Use:

- `GET /state/steps`
- `GET /artifacts/posts`
- `GET /artifacts/post`

Show:

- step selector
- paginated file list
- artifact JSON viewer

### Protocol / Debug Tools

Use:

- `POST /tools/protocol/data-load-preview`
- `POST /tools/protocol/research-preview`
- `POST /tools/protocol/angles-preview`
- `POST /tools/protocol/gen-terms`
- `POST /workflows/validate-post`
- `GET /workflows/double-process-posts`
- `POST /workflows/batch-angles-determinism`

Show:

- per-stage preview cards
- hashes and mismatch indicators
- expandable raw JSON

### Metrics

Use:

- `POST /tools/metrics/perplexity`
- `POST /tools/metrics/divergence`
- `POST /tools/metrics/post`
- `GET /tools/metrics/history`
- `DELETE /tools/metrics/sample`
- `GET /state/fs/read-json?path=...`

Show:

- run metrics buttons
- history table
- report detail viewer
- delete sample action with confirmation

### Prompts / Admin

Use:

- `GET /prompts/workflow-llm`
- `PUT /prompts/workflow-llm`
- `POST /prompts/workflow-llm/reset`
- `GET /admin/cache/stats`
- `POST /admin/cache/clear`

Show:

- prompt JSON editor
- reset button
- cache stats
- cache clear controls

## 4. Main Endpoints the Frontend Will Actually Use

## Read-only app state

| Endpoint | Use |
|---|---|
| `GET /health` | Boot check |
| `GET /state/steps` | Build step dropdowns |
| `GET /state/paths` | Show known dirs/files |
| `GET /state/logs` | Log file status |
| `GET /logging/tags` | Log filter metadata |
| `GET /state/recent-updates?days=&limit=` | Recent repo activity |

## Workflow execution

| Endpoint | Use |
|---|---|
| `GET /workflows/pipelines` | Discover commands |
| `GET /workflows/runs` | Show active work |
| `POST /workflows/run` | One generic execution entrypoint |
| `POST /workflows/data-load` | Prepare source posts |
| `POST /workflows/research` | Add research/search results |
| `POST /workflows/gen-angles` | Generate angles |
| `POST /workflows/stego` | Encode payload |
| `POST /workflows/decode` | Decode from text+angles |
| `POST /workflows/receiver` | Full receiver path |
| `POST /workflows/stego-receiver-live` | Full sender+receiver simulation |
| `POST /workflows/validate-post` | Reproducibility check |
| `POST /workflows/double-process-new-post` | Two-pass comparison |
| `GET /workflows/double-process-posts` | Read saved comparison history |

## Artifact browsing

| Endpoint | Use |
|---|---|
| `GET /artifacts/posts` | List files for a step |
| `GET /artifacts/post` | Load one artifact |

## Tooling

| Endpoint | Use |
|---|---|
| `POST /tools/protocol/data-load-preview` | Preview fetch step |
| `POST /tools/protocol/research-preview` | Preview research step |
| `POST /tools/protocol/angles-preview` | Preview angle generation |
| `POST /tools/protocol/gen-terms` | Preview generated search terms |
| `POST /tools/angles/analyze` | Standalone angle extraction |
| `POST /tools/fetch-url` | Raw fetch tool |
| `POST /tools/semantic/search` | Semantic ranking |
| `POST /tools/semantic/needle` | Best-match lookup |

## 5. Important Request Shapes

### `POST /workflows/stego`

```json
{
  "post_id": "optional",
  "payload": "optional or JSON value",
  "tag": "version_42",
  "run_all": false,
  "list_offset": 1,
  "max_posts": 10,
  "stream": true
}
```

Notes:

- `payload` can be a string or JSON; backend coerces it to string.
- `post_id` and `run_all=true` should not be combined in UI.

### `POST /workflows/receiver`

```json
{
  "post": {},
  "sender_user_id": "sender123",
  "compressed_bitstring": null,
  "allow_fallback": false,
  "fail_on_context_drift": true,
  "strict_decode": false,
  "use_fetch_cache": true,
  "use_terms_cache": true,
  "persist_terms_cache": true,
  "use_fetch_cache_research": true,
  "max_padding_bits": 256,
  "stream": true
}
```

### `POST /workflows/validate-post`

```json
{
  "post_id": "abc123",
  "use_terms_cache": false,
  "persist_terms_cache": false,
  "use_fetch_cache": false,
  "allow_angles_fallback": false,
  "stream": true
}
```

UI interpretation:

- `validation_outcome = protocol_match` => green
- `validation_outcome = protocol_mismatch` => yellow/red with diff details
- `validation_outcome = rerun_incomplete` => red/operator attention

### `POST /workflows/decode`

```json
{
  "stego_text": "text to decode",
  "angles": a],
  "few_shots": a],
  "strict_mode": false,
  "stream": true
}
```

## 6. Recent Updates API

Use `GET /state/recent-updates?days=7&limit=20`.

Response `data` shape:

- `generated_at_utc`
- `days`
- `limit`
- `count`
- `authors`
- `top_paths`
- `commits`

Each commit includes:

- `commit`
- `short_commit`
- `author`
- `committed_at`
- `subject`
- `files_changed_total`
- `files_changed_visible`
- `generated_files_changed`
- `insertions`
- `deletions`
- `paths`
- `has_more_paths`

Good UI:

- compact commit list
- author/date/subject first
- expandable changed paths

## 7. UI Rules That Matter

- Default to SSE for workflows.
- Keep raw JSON visible somewhere for operator workflows.
- Separate safe read-only actions from mutating/admin actions.
- Put cache controls, prompt editing, and delete actions behind clear confirmations.
- For large responses like `research` with `include_breakdown=true`, use collapsed sections and lazy rendering.
- For artifact browsing, paginate and avoid loading full JSON until the user selects one item.
- For validation and double-process screens, show stage-by-stage rows instead of a single status badge.

## 8. Suggested Screen Priority

Build in this order:

1. Dashboard
2. Workflow Runner
3. Artifact Explorer
4. Validation / Protocol Debugger
5. Metrics
6. Prompt/Admin tools

## 9. What the Frontend Can Ignore for V1

- Direct filesystem write/delete endpoints unless you are building an internal admin console
- KV endpoints unless you need operator key/value storage
- Low-level search endpoints unless you want separate diagnostics pages

## 10. Recommended Frontend Strategy

Use two API helpers:

- `requestJson()` for normal endpoints
- `requestSseWorkflow()` for workflow endpoints

The UI should feel like an operator console, not a consumer app:

- left side: controls/form
- center: live progress
- right side or drawer: raw response JSON

That layout matches how this backend behaves today.

## 11. How To Showcase Run Files

For a run like the current one, the UI should not imply success just because new files exist.

Example of this run:

- prep artifact created: `datasets/news_url_fetched/1noe8xb.json`
- prep artifact created: `datasets/news_researched/1noe8xb.json`
- automation logs created: `metrics/automation_logs/quota_20260429.stderr.log`
- automation logs created: `metrics/automation_logs/quota_20260429.stdout.log`
- no new finished sample in `output-results/`

Frontend rule:

- Show these as `prep artifacts` and `run logs`
- Show `output sample: none created` as a separate status
- Do not label the run as complete stego success unless a new file exists in `output-results/`

Recommended run detail layout:

- `Status summary`
  - prep completed
  - logs available
  - final output missing
- `Artifacts created`
  - data-load artifact JSON
  - research artifact JSON
- `Logs`
  - stdout log
  - stderr log
- `Final outputs`
  - list new files from `output-results`
  - if empty, show a warning state

Suggested badges:

- `Prep complete`
- `Research complete`
- `Logs captured`
- `No final sample`

Good operator wording:

- `This run prepared post data and captured logs, but did not produce a new final stego sample.`

Bad wording:

- `Run succeeded`
- `Output generated`

If you want this fully data-driven, the backend should eventually expose a run-summary endpoint that groups:

- created prep artifacts
- created logs
- created final outputs
- final run outcome

Right now, the frontend will need to derive that view from file listings and workflow results.

