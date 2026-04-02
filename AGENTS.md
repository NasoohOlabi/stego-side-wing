# Agent rules

- Append the user’s prompt to the end of `./prompts.md` after completing substantive work (or when the prompt is worth keeping for context).

# Project

**stego-side-wing** — Python backend and workflow runtime for stego pipelines. Package manager: **uv** (`pyproject.toml`, `uv.lock`). Python **3.13+**.

## Commands

| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| API | `uv run python src/API.py` (wrapper → `src/app/app_factory.py`; host/port from `API_HOST`, `API_PORT`) |
| API dev | `uv run python src/API.py --dev --host 127.0.0.1 --port 5001` or `API_DEBUG=1` |
| Workflow CLI | `uv run python src/scripts/workflow_cli.py -h` |
| Tests | `uv run pytest -q` (from repo root) |
| Types | `uv run pyright` (`pyrightconfig.json`) |

## Workspace conventions

- **Cursor rules** (repo-specific standards): read `.cursor/rules/` — especially **sender–receiver testing**, **python-architecture** (Pydantic v2, `@validate_call` on critical logic), **maintainability** (e.g. function length), **jsonl-observability** (structured logging, no `print`).
- **Repo root** is the normal cwd for `uv run` commands, pytest, and path resolution (`REPO_ROOT` in `src/infrastructure/config.py`).

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

- `docs/` — API and operator-facing spec ([`docs/api-spec.md`](docs/api-spec.md))
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
