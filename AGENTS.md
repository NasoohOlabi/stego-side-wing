# Agent rules

- Append the user’s prompt to the end of `./prompts.md` after completing substantive work (or when the prompt is worth keeping for context).

# Project

**stego-side-wing** — Python backend and workflow runtime for stego pipelines. Package manager: **uv** (`pyproject.toml`, `uv.lock`). Python **3.13+**.

## Commands

| Task | Command |
|------|---------|
| Install deps | `uv sync` |
| API | `uv run python src/API.py` (wrapper → `src/app/app_factory.py`; defaults `API_HOST`, `API_PORT`) |
| API dev | `uv run python src/API.py --dev --host 127.0.0.1 --port 5001` or `API_DEBUG=1` |
| Workflow CLI | `uv run python src/scripts/workflow_cli.py -h` (`main.py` forwards here) |
| Tests | `uv run pytest -q` |
| Types | `uv run pyright` (`pyrightconfig.json`) |

## Pyright strict scope

Strict: `src/app`, `src/services`, `src/pipelines`, `src/integrations`, `src/infrastructure`, `src/workflows`.  
Excluded (non-strict): e.g. `src/util`, `src/angles`, `src/**/__pycache__`.

## Layout (high level)

- `src/app/` — Flask app factory, routes, schemas
- `src/workflows/` — workflow runner, pipelines, adapters, contracts
- `src/pipelines/` — legacy/angle and scraper-related pipeline code
- `src/services/` — domain services
- `src/integrations/` — external APIs
- `src/infrastructure/` — config, cache, logging, shared infra
- `src/tests/` — pytest

Credentials and optional keys: see **README.md** (“Optional env vars”); use `.env` locally (`python-dotenv`).
