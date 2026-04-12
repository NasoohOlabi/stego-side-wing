# Agent rules

- Append the user’s prompt to the end of `./prompts.md` after completing substantive work (or when the prompt is worth keeping for context). That file is **gitignored** (local-only); create it at the repo root if it does not exist yet.

# Project

**stego-side-wing** — Python backend and workflow runtime for stego pipelines. Package manager: **uv** (`pyproject.toml`, `uv.lock`). Python **3.13+**.

## Commands

| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| API | `uv run python src/API.py` (wrapper → `src/app/app_factory.py`; host/port from env `API_HOST` / `API_PORT` or CLI; defaults **127.0.0.1** / **5001** when unset) |
| API dev | `uv run python src/API.py --dev --host 127.0.0.1 --port 5001` or `API_DEBUG=1` |
| Workflow CLI | `uv run python src/scripts/workflow_cli.py -h` |
| Tests | `uv run pytest -q` (from repo root) |
| Types | `uv run pyright` (`pyrightconfig.json`) |

## Workspace conventions

- **Cursor rules** (repo-specific standards): read `.cursor/rules/` — especially `sender-receiver-testing.mdc`, **python-architecture** (Pydantic v2, `@validate_call` on critical logic), **maintainability** (e.g. function length), **jsonl-observability** (structured logging, no `print`). For non-trivial edits, finish with full `uv run pytest -q` and `uv run pyright` (see `sender-receiver-testing.mdc` for targeted vs full runs).
- **Repo root** is the normal cwd for `uv run` commands, pytest, and path resolution (`REPO_ROOT` in `src/infrastructure/config.py`).

## Workflow LLM backend (global)

One switch chooses how most workflow and pipeline code talks to an LLM:

- **`WORKFLOW_LLM_BACKEND`** (in `.env` / process env; read via `infrastructure.config.get_workflow_llm_backend()`):
  - **`ai_studio`** (default if unset), or aliases **`google`** / **`gemini`** (case-insensitive): use **Google AI Studio** / Generative Language API through **`LLMAdapter`** with the **`google.genai`** client (`provider` `"gemini"`). Requires at least one of **`GOOGLE_PALM_API_KEY`**, **`GOOGLE_AI_API_KEYS`**, or **`GOOGLE_AI_API_KEY`**. Optional **`GOOGLE_AI_STUDIO_MODEL`** overrides the default model id.
  - **`lm_studio`** (or any other value): use the **OpenAI-compatible** server at **`LM_STUDIO_URL`** (normalized to include `/v1` in `get_lm_studio_url()`). Optional **`LM_STUDIO_API_TOKEN`** / **`LM_STUDIO_REQUEST_TIMEOUT_SEC`** where applicable.

- **Resolver**: `infrastructure.config.resolve_workflow_llm_provider_and_model(lm_model)` returns `(provider, model)` for `LLMAdapter.call_llm` — when the backend is Google, the `lm_model` argument is ignored in favor of `GOOGLE_AI_STUDIO_MODEL`.

- **Contributor rule**: Do not add ad-hoc `OpenAI(base_url=get_lm_studio_url(), ...)` for workflow-style calls. Prefer **`LLMAdapter`** + **`resolve_workflow_llm_provider_and_model`** (or the same env reads) so behavior stays consistent with retries, logging, and backend switching. Exceptions should be documented (e.g. third-party tools that only accept an OpenAI-compatible URL).

## Stego architecture (high level)

- **Sender** path: embed payload into workflow output (e.g. `StegoPipeline`, `workflows.utils.stego_codec.augment_post` and related pure functions).
- **Receiver** path: locate sender’s stego comment, `ReceiverPipeline.rebuild_context`, then `decode_payload`.
- **Shared contract** (must stay consistent across both sides): `src/workflows/utils/stego_codec.py` — not alternate encoding rules in pipelines beyond I/O and orchestration.
- After codec or pipeline contract changes: run targeted tests (`test_stego_codec.py`, `test_receiver_pipeline.py`, `test_pipeline_stego.py`, `test_api_v1_*`) and keep sender/receiver symmetry in mind (details in sender–receiver rule).

## Imports and entrypoints

- Library layout is under **`src/`** with imports like `from app...`, `from workflows...`, `from infrastructure...` (no `src.` prefix) when `src` is on `PYTHONPATH` (as in normal test/API runs).
- **Workflow CLI** canonical path: `src/scripts/workflow_cli.py`. Root `main.py` does `from scripts.workflow_cli import main`; that import expects `src` on `PYTHONPATH`. If `ModuleNotFoundError`, use `uv run python src/scripts/workflow_cli.py` or set `PYTHONPATH=src` for `main.py`.

## Pyright strict scope

**Strict:** `src/app`, `src/services`, `src/pipelines`, `src/integrations`, `src/infrastructure`, `src/workflows`.

**Excluded (non-strict):** `src/util`, `src/angles`, `src/**/__pycache__`, and these **top-level modules under `src/`:** `ai_analyze.py`, `headless_browser_analyzer.py`, `scraper.py`, `nest.py`.

## Layout (high level)

- `docs/` — API and operator-facing spec ([`docs/api-spec.md`](docs/api-spec.md)); workflow LLM copy lives in `config/workflow_llm_prompts.json` (see API spec / prompts routes)
- `scripts/` — **repo-root** standalone scripts (e.g. `avg_perplexity.py`, `avg_kld.py`; run as `uv run python scripts/<name>.py`)
- `metrics/` — default output for metrics JSON reports (created on first run)
- `src/app/` — Flask app factory, routes (e.g. `routes/api_v1_routes.py`), schemas
- `src/workflows/` — workflow runner, pipelines, adapters, contracts; **`src/workflows/utils/`** — stego codec and shared helpers
- `src/pipelines/` — legacy/angle and scraper-related pipeline code
- `src/services/` — domain services
- `src/integrations/` — external APIs
- `src/infrastructure/` — config, cache, logging, shared infra
- `src/tests/` — pytest
- `src/scripts/` — workflow CLI (package with `__init__.py`), distinct from repo-root `scripts/`

Credentials and optional keys: see **README.md** (“Optional env vars”); use `.env` locally (`python-dotenv`).
