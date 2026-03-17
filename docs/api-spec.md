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

- `POST /workflows/run`
  - Generic workflow runner.
  - Body: `command` (`data-load|research|gen-angles|stego|decode|gen-terms|full`) + same fields as the matching dedicated endpoint.
  - For `command: "stego"`, it uses the same optional/fallback semantics as `POST /workflows/stego`.
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
  - Body: `post_id?`, `payload?`, `tag?`, `list_offset?`
  - Behavior:
    - `post_id` is optional.
    - When `post_id` is omitted, the API auto-selects the next unprocessed post from `final-step` for the same `tag`.
    - If a provided `post_id` is not found in `final-step` or `angles-step`, it falls back to the same auto-selection behavior.
    - `payload` is optional; when omitted, the workflow uses the default payload from `workflows/27rZrYtywu3k9e7Q.json` (`SetSecretData.payload`).
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/decode`
  - Body: `stego_text` (string), `angles` (array), `few_shots?` (array)
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/gen-terms`
  - Body: `post_id` (string), `post_title?`, `post_text?`, `post_url?`
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

- `POST /workflows/full`
  - Body: `start_step?` (default `filter-url-unresolved`), `count?`
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.

### Tools

- `POST /tools/process-file`
  - Body: `name` (filename stem), `step`

- `POST /tools/fetch-url`
  - Body: `url`, `use_crawl4ai?` (bool)

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
