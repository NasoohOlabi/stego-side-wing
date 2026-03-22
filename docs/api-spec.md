# stego-side-wing API Spec (v1)

Base URL: `http://<host>:<port>/api/v1`  
Default local port: `5001`

## Response Contract

- Success envelope:
  - `ok: true`
  - `message?: string`
  - `data?: any`
- Error envelope:
  - `ok: false`
  - `error: string`
  - `details?: any`

## Auth

No auth is currently enforced in this service. Frontend should treat this API as trusted/internal.

## Endpoints

### Health

- `GET /health`
  - Returns service metadata and configured step count.

### State

- `GET /state/steps`
  - Returns configured pipeline `STEPS` mapping.

- `GET /state/paths`
  - Returns known state paths (datasets, caches, db files, logs).

- `GET /state/fs/list?path=<repo-relative>&recursive=<bool>&limit=<int>`
  - Lists files/directories under a repo-relative path.

- `GET /state/fs/read-json?path=<repo-relative-json-file>`
  - Reads a JSON file from repo scope.

- `POST /state/fs/write-json`
  - Body:
    - `path: string` (repo-relative `.json`)
    - `data: object`
    - `overwrite?: boolean` (default `true`)

- `DELETE /state/fs/delete?path=<repo-relative>&recursive=<bool>`
  - Deletes file, or directory when `recursive=true`.

### Artifacts

- `GET /artifacts/posts?step=<step>&count=<int>&offset=<int>&tag=<string?>`
  - Lists candidate post filenames for a step.

- `GET /artifacts/post?step=<step>&post=<filename>`
  - Fetches one post/artifact JSON by filename.

- `POST /artifacts/post?step=<step>`
  - Saves request JSON (must contain `id`) into step destination as `{id}.json`.

- `POST /artifacts/object?step=<step>&filename=<name.json>`
  - Saves request JSON as-is to step destination using provided filename.

### Workflows

- `GET /workflows/pipelines`
  - Returns available pipeline commands and workflow execution endpoints.

- `GET /workflows/runs`
  - Returns currently executing workflow runs in this API process.
  - Response `data`: `{ "runs": [...], "count": <int> }`.
  - Each run: `id`, `command`, `mode` (`sync` | `stream`), `started_at` (Unix seconds), `elapsed_ms`.

- `POST /workflows/run`
  - Generic workflow runner.
  - Body: `command` (`data-load|research|gen-angles|validate-post|stego|decode|gen-terms|full`) + same fields as the matching dedicated endpoint.
  - For `command: "stego"`, it uses the same optional/fallback semantics as `POST /workflows/stego` (including optional `payload` as a string or JSON value coerced to string).
  - For `command: "full"`, optional `payload` (string or JSON) is accepted and reported on the run as `payload_provided` in progress events; omit to use defaults where applicable.
  - Streaming:
    - Defaults to `text/event-stream` (SSE) with events: `status`, `progress`, `log`, `heartbeat`, `result`, `error`, `done`.
    - Disable streaming with `?stream=0` or body `{ "stream": false }` to get standard JSON envelope.

- `POST /workflows/data-load`
  - Body: `count?`, `offset?`, `batch_size?`
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/research`
  - Body: `count?`, `offset?`
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/gen-angles`
  - Body: `count?`, `offset?`
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/stego`
  - Body: `post_id?`, `payload?` (string or JSON object/array, coerced to string), `tag?`, `list_offset?`, `run_all?`, `max_posts?`
  - Behavior:
    - `post_id` is optional.
    - When `post_id` is omitted, the API auto-selects the next unprocessed post from `final-step` for the same `tag`.
    - If a provided `post_id` is not found in `final-step` or `angles-step`, it falls back to the same auto-selection behavior.
    - `payload` is optional; when omitted, the workflow uses the default payload from `workflows/27rZrYtywu3k9e7Q.json` (`SetSecretData.payload`).
    - `run_all` (default `false`) makes stego process posts recursively for the same tag until no unprocessed posts remain.
    - `max_posts` optionally limits how many posts are processed when `run_all=true`. Omitted, null, or any integer &lt; 1 means **no limit** (process until no unprocessed posts or a stop condition). Use `max_posts` ≥ 1 to cap batch size.
    - `post_id` cannot be combined with `run_all=true`.
  - `run_all` response shape:
    - `run_all`, `tag`, `list_offset`, `max_posts`
    - `processed_count`, `succeeded_count`, `failed_count`, `stopped_reason`
    - `results` (array of per-post stego outputs)
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/decode`
  - Body: `stego_text` (string), `angles` (array), `few_shots?` (array)
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/gen-terms`
  - Body: `post_id` (string), `post_title?`, `post_text?`, `post_url?`
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/validate-post`
  - Validates live protocol reproducibility for one post: reruns **data-load → research → gen-angles** in memory, then compares each stage’s live rerun payload to the saved artifact for that stage (strict deep JSON equality, including list order).
  - Body: `post_id` (string, required), `stream?` (bool; same SSE default as other workflow routes), `use_terms_cache?` (bool, default `false`), `persist_terms_cache?` (bool, default `false`), `use_fetch_cache?` (bool, default `false`), `allow_angles_fallback?` (bool, default `false`)
  - Prerequisites: baseline files must already exist for that `post_id` in each step’s destination directory (`{post_id}.json` per [`STEPS` in `infrastructure/config.py`](../src/infrastructure/config.py)); otherwise the handler returns 500 with a missing-baseline message.
  - Response `data` shape:
    - `post_id`: string
    - `mode`: `live_protocol_replay`
    - `settings`: effective replay flags
    - `valid`: boolean (true only if all three stages match)
    - `validation_outcome`: `protocol_match` | `protocol_mismatch` | `rerun_incomplete` — use this (not only `valid`) for UI labels: `rerun_incomplete` means a stage threw or was skipped, which is **not** the same as a baseline-vs-rerun data mismatch.
    - `validation_explanation`: short human-readable summary of `validation_outcome`
    - `steps`: object with keys `data_load`, `research`, `gen_angles`, each:
      - `step`: workflow step name
      - `comparison`: `match` | `mismatch` | `rerun_failed` | `skipped` — `mismatch` only when both baseline and rerun payloads were compared and differed; `rerun_failed` / `skipped` mean no comparison was performed
      - `matches`: boolean when a comparison ran (`true`/`false`); `null` when `comparison` is `rerun_failed` or `skipped` (generation failure is **not** a mismatch)
      - `comparison_note`: what this row means for operators
      - `changed_keys`: string paths (e.g. `search_results[1]`, `angles`) when `comparison` is `mismatch`; empty array when `match` or when not compared
      - `baseline_summary?`: stable hashes/counts for the saved artifact
      - `rerun_summary?`: stable hashes/counts for the live rerun payload
      - `protocol_report?`: detailed live protocol diagnostics for that stage
      - `error?`: stage failure reason; downstream stages are marked as skipped if an upstream stage fails
  - **Note:** This endpoint is now protocol-oriented. It does **not** overwrite saved artifacts during validation. LLM calls in this path use temperature 0 where applicable, but live web/search/provider drift can still cause `valid: false`.

- `POST /workflows/full`
  - Body: `start_step?` (default `filter-url-unresolved`), `count?`, `payload?` (optional string or JSON; same as stego `payload`)
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

### Tools

- `POST /tools/process-file`
  - Body: `name` (filename stem), `step`

- `POST /tools/fetch-url`
  - Body: `url`, `use_crawl4ai?` (bool)

- `POST /tools/protocol/gen-terms`
  - Body: `post_id` (required), `post_title?`, `post_text?`, `post_url?`, `use_cache?` (default `false`), `persist_cache?` (default `false`)
  - Returns generated search terms plus prompt/model hashes and cache usage metadata.

- `POST /tools/protocol/data-load-preview`
  - Body: `post_id` (required), `use_cache?` (default `false`)
  - Returns the live fetched `selftext` preview and fetch diagnostics without saving artifacts.

- `POST /tools/protocol/research-preview`
  - Body: `post_id` (required), `use_terms_cache?` (default `false`), `persist_terms_cache?` (default `false`), `use_fetch_cache?` (default `false`)
  - Runs live `data-load` then `research` in memory and returns generated terms, selected search results, fetched page hashes/previews, and final `search_results`.

- `POST /tools/protocol/angles-preview`
  - Body: `post_id` (required), `use_terms_cache?` (default `false`), `persist_terms_cache?` (default `false`), `use_fetch_cache?` (default `false`), `allow_angles_fallback?` (default `false`)
  - Runs live `data-load` + `research` + `gen-angles` in memory and returns angle-input hashes, prompt/model hashes, and generated angles without saving artifacts.

- `GET /tools/search/news?query=<q>`
- `GET /tools/search/ollama?query=<q>`
- `GET /tools/search/bing?query=<q>&first=<int>&count=<int>`
- `GET /tools/search/google?query=<q>&first=<int>&count=<int>`

- `POST /tools/semantic/search`
  - Body: `text`, `objects`, `n?`

- `POST /tools/semantic/needle`
  - Body: `needle`, `haystack` (string array)

- `POST /tools/angles/analyze`
  - Body: `texts` (string array)

### KV Store

- `GET /kv?limit=<int>&offset=<int>`
  - Lists key-value entries.

- `GET /kv/{key}`
  - Gets a single key.

- `PUT /kv/{key}`
  - Body: `{ "value": <any-json> }`

- `DELETE /kv/{key}`
  - Deletes key (idempotent).

### Admin

- `GET /admin/cache/stats`
  - Returns file-count/size stats for Flask/url/angles caches.

- `POST /admin/cache/clear`
  - Body: `{ "target": "flask" | "url" | "angles" | "all" }`

- `POST /admin/kv/migrate`
  - Runs legacy JSON-to-SQLite KV migration and init.

## Frontend Integration Notes

- Prefer only `/api/v1/*` endpoints for new UI work.
- Workflow endpoints now support SSE progress events by default, so clients can render live progress while long tasks execute.
- Errors from third-party providers are surfaced via `error` + optional `details`.
- Current API is internal/admin-capable and has no authorization layer.
