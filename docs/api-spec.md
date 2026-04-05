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

## Concepts

### Observability

- **File logs:** `GET /state/logs` and `DELETE /state/logs` expose the API JSONL log file path, on-disk size, and truncation. Truncation frees disk space and does not remove stderr logging; `DELETE` returns `400` when file logging is disabled (e.g. `--no-log-file`).
- **Structured tags:** `GET /logging/tags` returns the canonical list of structured log tag ids and descriptions used in JSONL output so clients and operators can align filters, dashboards, or docs with actual log fields.
- **Live runs:** `GET /workflows/runs` lists workflow runs currently executing in this API process (`id`, `command`, `mode`, timing).
- **Streaming (SSE):** Workflow routes that support streaming default to `text/event-stream` with events `status`, `progress`, `log`, `heartbeat`, `result`, `error`, `done`. Disable with `?stream=0` or body `{ "stream": false }` for a normal JSON envelope—useful for scripts or when a single response is enough.

### Testing and protocol QA

These endpoints support **reproducibility**, **main vs isolated-cache** comparison, and **LLM determinism** checks. They are documented in detail under **Workflows** and **Tools**; this section only maps intent:

- `**POST /workflows/validate-post`** — Replays data-load → research → gen-angles **in memory**, compares each stage’s live output to **saved** artifacts (strict JSON equality). Does **not** overwrite artifacts.
- `**POST /workflows/double-process-new-post`** — Picks one new post from the data-load queue, runs the three-stage pipeline **twice** (main caches vs isolated validation caches; cache **flags** stay enabled on both passes), **writes** artifacts each time, and reports per-stage hash match between passes.
- `**POST /workflows/batch-angles-determinism`** — For each `post_id`, runs angle extraction **twice** with angles disk cache disabled and compares normalized angle lists; measures same-host repeatability, not cross-machine parity.
- **Protocol previews** (`POST /tools/protocol/data-load-preview`, `research-preview`, `angles-preview`, `gen-terms`) — Live protocol steps with controlled persistence for inspecting behavior before full pipeline runs.
- `**POST /workflows/gen-angles`** — Batch angle generation over the `angles-step` queue (`count` / `offset`); persists via `GenAnglesPipeline.process_posts`.
- `**POST /workflows/receiver**` — Decode path: rebuilds context (**data-load → research → gen-angles**) then decodes the sender’s stego comment for a supplied post JSON.
- `**POST /workflows/stego-receiver-live`** — Runs **stego** then **receiver** with isolated sender/receiver disk caches; receiver rebuild still runs **gen-angles** as part of context rebuild.

### Workflow LLM prompts

- **On disk:** `[config/workflow_llm_prompts.json](../config/workflow_llm_prompts.json)` at the repository root. Pipelines read templates through `workflows.utils.workflow_llm_prompts` (in-process cache; **GET** reloads from disk before returning).
- **API:** `GET /prompts/workflow-llm` (read), `PUT /prompts/workflow-llm` (replace whole document), `POST /prompts/workflow-llm/reset` (restore code defaults). Same paths appear under `prompts.workflow_llm` in `GET /state/paths`.
- **Editing:** Templates are Python `str.format` strings. Keep every `{placeholder}` that the code supplies (see State → Workflow LLM prompts for the field list). A bad template causes runtime errors when a workflow step runs, not necessarily at PUT time beyond schema validation.

### Text and payload fields

Common **string-oriented** request fields across v1 (see each endpoint for full schemas):

- **Embedding / decoding:** optional `payload` on stego and full workflows (string or JSON value **coerced to string**); `stego_text` plus `angles` on decode.
- **Post text overrides:** `post_title`, `post_text`, `post_url` on `gen-terms` and protocol tools where listed.
- **Tools:** `text` on `POST /tools/semantic/search`; `needle` and `haystack` (string array) on `POST /tools/semantic/needle`; `texts` (string array) on `POST /tools/angles/analyze`.

### Stego metrics (perplexity, KL/JSD)

- **Purpose:** Score stego-bearing texts produced by the pipeline (typically under `output-results`, i.e. `final-step` in `[STEPS](../src/infrastructure/config.py)`) for language-model perplexity and for distributional distance (word unigrams) vs comment baselines.
- **On disk:** Each run writes a timestamped JSON file under `**metrics/`** at the repository root by default (`perplexity_metrics_<UTC>.json`, `divergence_metrics_<UTC>.json`). The same logic is shared with the CLI scripts `[scripts/avg_perplexity.py](../scripts/avg_perplexity.py)` and `[scripts/avg_kld.py](../scripts/avg_kld.py)` (they add `src/` to `sys.path` and default `--metrics-dir` to that folder).
- **API vs CLI:** POST endpoints run the same code paths as the scripts; `GET /tools/metrics/history` lists recent report files without loading full JSON bodies.
- **Dependencies:** Perplexity scoring needs `**torch`** and `**transformers**` (not pinned in the core `pyproject.toml` dependency list); install them in the same venv if you use `POST /tools/metrics/perplexity` or the perplexity script. Divergence metrics use only the standard library plus repo code.

## Endpoints

### Health

- `GET /health`
  - Returns service metadata and configured step count.

### State

- `GET /state/steps`
  - Returns configured pipeline `STEPS` mapping.
- `GET /state/paths`
  - Returns known state paths (datasets, caches, db files, logs, **metrics output**).
  - Includes `metrics.dir`: absolute path to the default metrics report directory (`<repo>/metrics`).
- `GET /state/logs`
  - Returns the configured API JSONL log file size and path.
  - Response `data`: `file_logging_enabled` (bool), `path` (string or null), `bytes` (integer, on-disk size).
- `DELETE /state/logs`
  - Truncates the API JSONL log file to zero bytes (frees disk space; does not remove stderr logging).
  - Returns `400` if file logging is disabled (e.g. `--no-log-file`).
- `GET /logging/tags`
  - Returns the canonical list of structured log tag ids and descriptions used in JSONL output.
  - Response `data`: `{ "tags": [ { "id": string, "description": string }, ... ], "tag_ids": string[] }`.
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
- **Workflow LLM prompts** (also listed under `prompts.workflow_llm` in `GET /state/paths`):
  - `GET /prompts/workflow-llm`
    - Reloads `[config/workflow_llm_prompts.json](../config/workflow_llm_prompts.json)` from disk, clears the in-process cache, then returns the merged document.
    - Response `data`: `{ "prompts": <object>, "path": "<string>" }`. If the file lives under the repo root, `path` is repo-relative (forward slashes); otherwise it is the absolute path (e.g. tests or custom layouts).
    - `prompts` shape: `version` (integer ≥ 1), plus:

      | Key                | Fields                                                                                 | Supplied placeholders                                                                                                                                         |
      | ------------------ | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
      | `stego_encode`     | `system_template`, `user_template`                                                     | System: `tangent`, `category`. User: `best_match`, `title`, `author`, `selftext`, `chain_section`.                                                            |
      | `stego_decode`     | `user_template`, `system_template`                                                     | User: `few_shots`, `stego_text`. System: `angle_count`, `candidates_json`.                                                                                    |
      | `gen_angles`       | `user_template`, `system_template`                                                     | User: `combined_text`. System: none (static).                                                                                                                 |
      | `gen_search_terms` | `system_template`, `user_title_template`, `user_url_template`, `user_content_template` | System: static. User segments: `title`, `url`, `text` (only non-empty segments are concatenated with blank lines, same order as before: title, url, content). |

  - `PUT /prompts/workflow-llm`
    - Body: `{ "prompts": <object> }` — **full** document (not a patch). Validates with the same schema as the loader, writes atomically (temp file + replace), reloads cache. Success: `201`, `message` optional, `data.path` and `data.written: true`.
    - Example (save edited JSON as `body.json` with top-level key `prompts`):
      ```bash
      curl -sS -X PUT "http://127.0.0.1:5001/api/v1/prompts/workflow-llm" \
        -H "Content-Type: application/json" \
        -d @body.json
      ```
  - `POST /prompts/workflow-llm/reset`
    - No body. Rewrites the JSON file from **baked-in defaults** (same as a fresh clone’s defaults) and reloads the cache. Use when the file is corrupted or you want to discard edits without using git.

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
  - Body: `command` (string) + the same fields as the matching dedicated `POST /workflows/<name>` route.
  - **Canonical command list:** `GET /workflows/pipelines` returns `commands` and `endpoints`; keep clients aligned with that list (includes e.g. `receiver`, `stego-receiver-live`, `batch-angles-determinism`).
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
  - Runs batch angle generation for the `**angles-step`** queue: `WorkflowRunner.run_gen_angles` → `GenAnglesPipeline.process_posts` with the given window.
  - Body: `count?` (default `1`), `offset?` (default `0`)
  - Response `data`: list of per-post pipeline results (same shape as other batch workflow returns).
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.
- `POST /workflows/stego`
  - Body: `post_id?`, `payload?` (string or JSON object/array, coerced to string), `tag?`, `list_offset?`, `run_all?`, `max_posts?`
  - Behavior:
    - `post_id` is optional.
    - When `post_id` is omitted, the API auto-selects the next unprocessed post from `final-step` for the same `tag`.
    - If a provided `post_id` is not found in `final-step` or `angles-step`, it falls back to the same auto-selection behavior.
    - `payload` is optional; when omitted, the workflow uses the default payload from `workflows/27rZrYtywu3k9e7Q.json` (`SetSecretData.payload`).
    - `run_all` (default `false`) makes stego process posts recursively for the same tag until no unprocessed posts remain.
    - `max_posts` optionally limits how many posts are processed when `run_all=true`. Omitted, null, or any integer < 1 means **no limit** (process until no unprocessed posts or a stop condition). Use `max_posts` ≥ 1 to cap batch size.
    - `post_id` cannot be combined with `run_all=true`.
  - `run_all` response shape:
    - `run_all`, `tag`, `list_offset`, `max_posts`
    - `processed_count`, `succeeded_count`, `failed_count`, `stopped_reason`
    - `results` (array of per-post stego outputs)
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.
- `POST /workflows/decode`
  - Body: `stego_text` (string), `angles` (array), `few_shots?` (array)
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.
- `POST /workflows/receiver`
  - Decodes a payload from a **full post JSON** and agreed `sender_user_id` (locates the sender’s stego comment, rebuilds receiver-side context, then decodes).
  - Body:
    - `post` (object, required) — post document including `comments` / `angles` as produced for the protocol.
    - `sender_user_id` (string, required)
    - `compressed_bitstring?` — optional compressed bitstring override for decode
    - `allow_fallback?` (bool, default `false`) — passed to gen-angles preview when rebuilding context
    - `use_fetch_cache?` (bool, default `true`), `use_terms_cache?` (bool, default `true`), `persist_terms_cache?` (bool, default `true`), `use_fetch_cache_research?` (bool, default `true`)
    - `max_padding_bits?` (int, default `256`, non-negative)
  - **Note:** Receiver rebuild runs **data-load → research → gen-angles** (`ReceiverPipeline.rebuild_context`) before decode.
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.
- `POST /workflows/stego-receiver-live`
  - **Purpose:** End-to-end **stego** then **receiver** with **disjoint on-disk URL/terms caches** for sender vs receiver (cold-receiver simulation). Uses isolated `WorkflowRunner` instances per side under a temp or user-provided root.
  - Body:
    - `sender_user_id` (string, required)
    - `post_id?` — if omitted, the workflow advances `list_offset` across attempts (see `max_post_attempts`)
    - `payload?` (string or JSON coerced to string, optional), `tag?`, `list_offset?` (int, default `1`) — same semantics as `POST /workflows/stego` for the sender leg
    - `simulation_root?` (string, optional) — repo-relative or absolute directory for simulation attempt folders; default is a process temp directory
    - `compressed_bitstring?` — optional override passed through to receiver decode
    - `allow_fallback?` (bool, default `false`)
    - `max_padding_bits?` (int, default `256`, non-negative)
    - `max_post_attempts?` (int, default `25`, ≥ `1`) — when `post_id` is omitted, number of list-offset tries (skips some fetch/quota failures per runner logic)
  - Response `data` (success path): `succeeded`, `stego`, `receiver`, `simulation` (paths and attempt metadata), plus `skipped_posts` when multi-post selection is used.
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.
- `POST /workflows/gen-terms`
  - Body: `post_id` (string), `post_title?`, `post_text?`, `post_url?`
  - Streaming defaults to SSE; disable via `?stream=0` or `{ "stream": false }`.
- `POST /workflows/validate-post`
  - Validates live protocol reproducibility for one post: reruns **data-load → research → gen-angles** in memory, then compares each stage’s live rerun payload to the saved artifact for that stage (strict deep JSON equality, including list order).
  - Body: `post_id` (string, required), `stream?` (bool; same SSE default as other workflow routes), `use_terms_cache?` (bool, default `false`), `persist_terms_cache?` (bool, default `false`), `use_fetch_cache?` (bool, default `false`), `allow_angles_fallback?` (bool, default `false`)
  - Prerequisites: baseline files must already exist for that `post_id` in each step’s destination directory (`{post_id}.json` per `[STEPS` in `infrastructure/config.py](../src/infrastructure/config.py)`); otherwise the handler returns 500 with a missing-baseline message.
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
- `POST /workflows/double-process-new-post`
  - **Purpose:** Pick one **new** post (same queue as data-load: JSON in `datasets/news_cleaned` with no matching `{id}.json` yet in `datasets/news_url_fetched`), then run the full three-stage pipeline **twice** on that same `post_id` to compare **main** URL/terms/angles caches vs an **isolated validation** cache namespace (both passes keep `use_fetch_cache` / `use_terms_cache` / `persist_terms_cache` **true**).
  - Body: `stream?` (bool; same SSE default as other workflow routes), `allow_angles_fallback?` (bool, default `false`) — passed through to gen-angles (same semantics as `validate-post` / `angles-preview`).
  - **Pass 1 (`pass_1_cached`):** Default workflow config — main `datasets/url_cache`, `datasets/research_terms_cache.db`, `datasets/angles_cache`.
  - **Pass 2 (`pass_2_validation`):** Same cache **flags** as pass 1, but the run is executed under `isolated_workflow_config` pointing at a dedicated tree: default `datasets/double_process_validation/` (`url_cache/`, `angles_cache/`, `research_terms_cache.db` under that root), or override the root with env `**DOUBLE_PROCESS_VALIDATION_ROOT`** (absolute or repo-relative path as resolved by the app). Pass 2 does not read pass 1’s cache files; an empty validation store yields cache misses and full fetch/LLM work while using the same code paths as a normal cached run.
  - **Persistence:** Unlike `validate-post`, this **writes** stage outputs to disk each time (same as running data-load → research → gen-angles manually). The second pass overwrites artifacts for that `post_id` in `filter-url-unresolved`, `filter-researched`, and `angles-step` destinations.
  - Response `data` shape (summary):
    - `mode`: `double_process_new_post`
    - `post_id`: string (stem of selected file)
    - `source_file`: e.g. `{post_id}.json` as listed by the queue
    - `passes.pass_1_cached` / `passes.pass_2_validation`: each has `settings` with the four cache flags above plus `cache_profile` (`main` | `validation`) and `cache_paths` (`url_cache_dir`, `research_terms_db_path`, `angles_cache_dir`), and `steps` with per-stage summaries (`data_load`, `research`, `gen_angles`) including stable `hash` and stage-specific fields (same summarizer as other workflow reports)
    - `stage_hash_match`: `{ "data_load": bool, "research": bool, "gen_angles": bool }` — whether the full-post hash for each stage matched between the two passes (search/API non-determinism often makes `research` differ between passes even on the same day)
  - Also available as `POST /workflows/run` with `"command": "double-process-new-post"` and the same body fields.
- `POST /workflows/batch-angles-determinism`
  - **Purpose:** For each `post_id`, load the post from `step` (default `angles-step`), build the same text dictionary as gen-angles, then run angle extraction **twice** with **angles disk cache disabled** (`use_cache=false` on `analyze_angles_from_texts`) and compare normalized angle lists (non-empty `source_quote` / `tangent` / `category` only, same as production preview).
  - Body: `post_ids` (required non-empty string array), `step?` (default `angles-step`), `stream?` (bool; same SSE default as other workflow routes).
  - Response `data`: `mode` (`batch_angles_determinism`), `posts_requested`, `posts_succeeded`, `all_identical` (true only if every row without `error` has `identical: true`), `results[]` per post (`run_1_hash` / `run_2_hash`, `identical`, `error?`, etc.).
  - **Note:** This does **not** prove cross-machine parity; it only measures whether two fresh runs on **this** host+LLM return the same normalized list for the same inputs.
  - Also available as `POST /workflows/run` with `"command": "batch-angles-determinism"` and the same body fields.
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
- `POST /tools/metrics/perplexity`
  - Computes sliding-window **GPT-style perplexity** (causal LM negative log-likelihood) over stego text in each `*.json` under `output_dir`, writes a report JSON under `metrics_dir`, and returns the full report plus the absolute `report_path`.
  - **Input extraction:** Accepts either a JSON **object** with `stego_text` or `stegoText`, or a JSON **array** whose first element is an object with `stegoText` (same flexibility as `[scripts/avg_perplexity.py](../scripts/avg_perplexity.py)`).
  - **Body (JSON):**
    - `output_dir?` — repo-relative directory of pipeline output files (default `output-results`). Must exist and be a directory.
    - `metrics_dir?` — repo-relative directory for report files (default `<repo>/metrics`). Created if missing.
    - `model_name?` — Hugging Face causal LM id (default `gpt2`).
    - `stride?` — sliding window stride in tokens (default `512`, must be ≥ `1`).
    - `device?` — `auto` | `cpu` | `cuda` (default `auto`).
  - **Success `data`:** `{ "report": <object>, "report_path": <string> }` — `report` includes `created_at_utc`, `config`, `dataset_summary`, `perplexity_summary`, `per_file_perplexity`.
  - **Errors:** `400` (invalid args, no usable texts, no valid scores), `404` (`output_dir` missing), `501` (missing `torch` / `transformers`), `500` (other failures).
  - **Example:**
    ```bash
    curl -sS -X POST 'http://127.0.0.1:5001/api/v1/tools/metrics/perplexity' \
      -H 'Content-Type: application/json' \
      -d '{"output_dir":"output-results","model_name":"gpt2","device":"cpu"}'
    ```
- `POST /tools/metrics/divergence`
  - Computes smoothed word-unigram **KL(stego ∥ baseline)** and **JSD** vs (1) comment text on the **matched post** JSON in `dataset_dir` and (2) a **global** distribution over all comment bodies in `dataset_dir`. Writes `divergence_metrics_<UTC>.json` under `metrics_dir`.
  - **Input extraction:** Each output file must be a JSON **array** with a first object containing string `stegoText`. Post id for matching is `filename` stem split on `_version_` (first segment), consistent with `[scripts/avg_kld.py](../scripts/avg_kld.py)`.
  - **Body (JSON):**
    - `output_dir?` (default `output-results`)
    - `dataset_dir?` — repo-relative post JSON directory (default `datasets/news_cleaned`)
    - `metrics_dir?` (default `<repo>/metrics`)
    - `alpha?` — Dirichlet-style smoothing (default `1e-6`, must be > `0`)
  - **Success `data`:** `{ "report": <object>, "report_path": <string> }` — `report` includes `dataset_summary`, `primary_baseline_matched_post`, `secondary_baseline_global_corpus`.
  - **Errors:** `400` (invalid args, no usable stego samples), `404` (missing `output_dir` or `dataset_dir`), `500` (other failures).
  - **Example:**
    ```bash
    curl -sS -X POST 'http://127.0.0.1:5001/api/v1/tools/metrics/divergence' \
      -H 'Content-Type: application/json' \
      -d '{"output_dir":"output-results","dataset_dir":"datasets/news_cleaned"}'
    ```
- `POST /tools/metrics/post`
  - For **one** pipeline output JSON under `output_dir`, extracts stego text once and returns **perplexity** (same causal-LM sliding-window method as batch perplexity) plus word-unigram **KL(stego ∥ baseline)** and **JSD** vs the matched post in `dataset_dir` and vs the **global** comment corpus over `dataset_dir` (same logic as `POST /tools/metrics/divergence`). Does **not** write a report under `metrics/` (response-only).
  - **Stego extraction:** JSON array `[{ "stegoText": ... }]` first, else object `stegoText` / `stego_text`.
  - **Body (JSON):**
    - `filename` (required) — basename only, must end with `.json` (e.g. `1look5n_version_15.json`). No path separators.
    - `output_dir?` (default `output-results`)
    - `dataset_dir?` (default `datasets/news_cleaned`)
    - `model_name?`, `stride?`, `device?` — same as perplexity endpoint
    - `alpha?` — same as divergence (default `1e-6`, must be > `0`)
  - **Success `data`:** `file` (repo-relative path when under repo root), `post_id`, `perplexity` (number or `null` if `torch`/`transformers` missing or scoring failed), `resolved_device`, `primary_baseline_matched_post`, `secondary_baseline_global_corpus`, `warnings` (string array), `config`.
  - **Errors:** `400` (invalid `filename`, bad JSON in output file, missing stego), `404` (output file or `dataset_dir` missing), `500` (other failures).
  - **Example:**
    ```bash
    curl -sS -X POST 'http://127.0.0.1:5001/api/v1/tools/metrics/post' \
      -H 'Content-Type: application/json' \
      -d '{"filename":"1look5n_version_15.json","output_dir":"output-results","dataset_dir":"datasets/news_cleaned","device":"cpu"}'
    ```
- `GET /tools/metrics/history`
  - Lists saved metrics report files **newest first** (by filesystem mtime). Does not parse report JSON; use `GET /state/fs/read-json?path=<path>` if you need the full document.
  - **Query parameters:**
    - `type` — `all` (default), `perplexity`, or `divergence` (filters by filename pattern).
    - `limit` — max rows (default `50`, capped at `200`).
    - `metrics_dir` — optional repo-relative override (default `<repo>/metrics`). If the directory does not exist, `history` is `[]`.
  - **Success `data`:** `{ "metrics_dir", "type", "limit", "count", "history" }` where each `history[]` item is `{ "kind", "filename", "path", "size_bytes", "updated_at_utc" }` (`path` is repo-relative with forward slashes).
  - **Example:**
    ```bash
    curl -sS 'http://127.0.0.1:5001/api/v1/tools/metrics/history?type=all&limit=20'
    ```

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
- **Metrics UI:** Use `GET /tools/metrics/history` for a file list, then `GET /state/fs/read-json?path=…` with the returned `path` to load a report. Perplexity runs can be slow and may require GPU memory; consider `device=cpu` or a smaller `model_name` for lighter hosts.

