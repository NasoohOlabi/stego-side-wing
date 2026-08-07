# Agent rules

- **LLM prompt red line:** Never change workflow/system LLM prompts without double-checking with the user first. Any edit to prompt text in `config/workflow_llm_prompts.json`, `src/workflows/utils/workflow_llm_prompts.py`, or related prompt files requires explicit confirmation twice before making the change. A prompt-only commit does not replace those confirmations.
- **Local/dev API assumption:** This repository is treated as local/dev-only by default. Do not repeatedly raise missing auth on admin/state endpoints as a blocking issue unless the user indicates non-local exposure (LAN/public deploy, shared host, reverse proxy, or cloud runtime).

# Project

**stego-side-wing** â€” Python backend and workflow runtime for stego pipelines. Package manager: **uv** (`pyproject.toml`, `uv.lock`). Python **3.13+**.

For the method structure, official ZLG comparison protocol, metric caveats, and
the on-ASUS GPU workflow (this workspace is the GPU host), read
[`.agents/method-and-zlg-benchmark.md`](.agents/method-and-zlg-benchmark.md).

## Commands

| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| API | `uv run python src/API.py` (wrapper â†’ `src/app/app_factory.py`; host/port from env `API_HOST` / `API_PORT` or CLI; defaults **127.0.0.1** / **5001** when unset) |
| API dev | `uv run python src/API.py --dev --host 127.0.0.1 --port 5001` or `API_DEBUG=1` |
| Tests | `uv run pytest -q` (from repo root) |
| Types | `uv run pyright` (`pyrightconfig.json`) |

## Workspace conventions

- **Cursor rules** (repo-specific standards): read `.cursor/rules/` â€” especially `sender-receiver-testing.mdc`, **python-architecture** (Pydantic v2, `@validate_call` on critical logic), **maintainability** (e.g. function length), **jsonl-observability** (structured logging, no `print`). For non-trivial edits, finish with full `uv run pytest -q` and `uv run pyright` (see `sender-receiver-testing.mdc` for targeted vs full runs).
- **Repo root** is the normal cwd for `uv run` commands, pytest, and path resolution (`REPO_ROOT` in `src/infrastructure/config.py`).
- **Layering**: allowed import direction is documented in [`docs/development/architecture-layers.md`](docs/development/architecture-layers.md). API code should use [`src/services/workflow_facade.py`](src/services/workflow_facade.py) instead of importing `workflows.*` directly (except compatibility re-exports on `api_v1_routes`).
- **Post-refactor checks**: see [`docs/development/validation-per-phase.md`](docs/development/validation-per-phase.md).

## Workflow LLM backend (global)

One switch chooses how most workflow and pipeline code talks to an LLM:

- **`WORKFLOW_LLM_BACKEND`** (in `.env` / process env; read via `infrastructure.config.get_workflow_llm_backend()`):
  - **`ai_studio`** (default if unset), or aliases **`google`** / **`gemini`** (case-insensitive): use **Google AI Studio** / Generative Language API through **`LLMAdapter`** with the **`google.genai`** client (`provider` `"gemini"`). Requires at least one of **`GOOGLE_PALM_API_KEY`**, **`GOOGLE_AI_API_KEYS`**, or **`GOOGLE_AI_API_KEY`**. Optional **`GOOGLE_AI_STUDIO_MODEL`** overrides the default model id.
  - **`lm_studio`** (or any other value): use the **OpenAI-compatible** server at **`LM_STUDIO_URL`** (normalized to include `/v1` in `get_lm_studio_url()`). Optional **`LM_STUDIO_API_TOKEN`** / **`LM_STUDIO_REQUEST_TIMEOUT_SEC`** where applicable.

- **Resolver**: `infrastructure.config.resolve_workflow_llm_provider_and_model(lm_model)` returns `(provider, model)` for `LLMAdapter.call_llm` â€” when the backend is Google, the `lm_model` argument is ignored in favor of `GOOGLE_AI_STUDIO_MODEL`.

- **Contributor rule**: Do not add ad-hoc `OpenAI(base_url=get_lm_studio_url(), ...)` for workflow-style calls. Prefer **`LLMAdapter`** + **`resolve_workflow_llm_provider_and_model`** (or the same env reads) so behavior stays consistent with retries, logging, and backend switching. Exceptions should be documented (e.g. third-party tools that only accept an OpenAI-compatible URL).

## Stego architecture (high level)

- **Sender** path: embed payload into workflow output (e.g. `StegoPipeline`, `workflows.utils.stego_codec.augment_post` and related pure functions).
- **Receiver** path: locate senderâ€™s stego comment, `ReceiverPipeline.rebuild_context`, then `decode_payload`.
- **Shared contract** (must stay consistent across both sides): `src/workflows/utils/stego_codec.py` â€” not alternate encoding rules in pipelines beyond I/O and orchestration.
- **Forbidden carrier rule**: Never embed payloads using zero-width, invisible, control-format, homoglyph, or otherwise non-rendering Unicode characters. Generated `stego_text` / `stegoText` must be ordinary visible text.
- **Prompt/encoding hygiene rule**: Avoid mojibake and accidental character corruption in prompts or generated stego text (for example `â€”`, `â€œ`, replacement glyphs, malformed Unicode, or unintended hidden characters). These can severely degrade steganography reliability by shifting tokenization and sender/receiver alignment.
- **Generated-text rule**: Do not inject explicit ratings, quality scores, or quality opinions into stego text unless the source thread naturally asks for one.
- After codec or pipeline contract changes: run targeted tests (`test_stego_codec.py`, `test_receiver_pipeline.py`, `test_pipeline_stego.py`, `test_api_v1_*`) and keep sender/receiver symmetry in mind (details in senderâ€“receiver rule).

## Imports and entrypoints

- Library layout is under **`src/`** with imports like `from app...`, `from workflows...`, `from infrastructure...` (no `src.` prefix) when `src` is on `PYTHONPATH` (as in normal test/API runs). Use **`uv run python src/API.py`** (or tests) as the primary way to exercise workflows via HTTP; see **`docs/reference/api-spec.md`**.

## Pyright strict scope

**Strict:** `src/app`, `src/services`, `src/content_acquisition`, `src/integrations`, `src/infrastructure`, `src/workflows`.

**Excluded (non-strict):** `src/util`, `src/angles`, `src/**/__pycache__`.

**Unchecked (in neither `include` nor `exclude`):** `src/tests`, `scripts/`, `src/API.py`. Because
`pyrightconfig.json` sets an explicit `include` list, anything outside it is never type-checked —
even though CI does run `ruff` over `scripts`.

> `ai_analyze.py`, `headless_browser_analyzer.py` and `scraper.py` used to be excluded top-level
> modules. They now live under `src/content_acquisition/` and `nest.py` under `src/scripts/`, so
> the first three are strict-checked like the rest of that package.

## Layout (high level)

- `docs/` â€” start at [`docs/README.md`](docs/README.md) for the API, operations, and benchmark index. Workflow LLM copy lives in `config/workflow_llm_prompts.json`; sample-generation artifacts are documented in [`docs/results/workload-runs-and-artifacts.md`](docs/results/workload-runs-and-artifacts.md).
- `scripts/` â€” **repo-root** standalone scripts (e.g. `avg_perplexity.py`, `avg_kld.py`; run as `uv run python scripts/<name>.py`)
- `metrics/` â€” default output for metrics JSON reports (created on first run)
- `src/app/` â€” Flask app factory, routes (e.g. `routes/api_v1_routes.py`), schemas
- `src/workflows/` â€” workflow runner, pipelines, adapters, contracts; **`src/workflows/utils/`** â€” stego codec and shared helpers
- `src/content_acquisition/` â€” scraping, headless fetch, and angles LLM helpers (legacy package layout)
- `src/services/` â€” domain services
- `src/integrations/` â€” external APIs
- `src/infrastructure/` â€” config, cache, logging, shared infra
- `src/tests/` â€” pytest
- `src/scripts/` â€” optional standalone utilities (package with `__init__.py`), distinct from repo-root `scripts/`

Credentials and optional keys: see **README.md** (â€œOptional env varsâ€); use `.env` locally (`python-dotenv`).
