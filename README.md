# stego-side-wing

Python backend and workflow runtime for the stego pipelines.

## Requirements

- Python `3.13+`
- [uv](https://docs.astral.sh/uv/)

## Setup (uv-managed)

```bash
uv sync
```

This project uses `pyproject.toml` + `uv.lock`; dependencies are tracked and reproducible through `uv`.

## Run the API

```bash
uv run python src/API.py
```

`src/API.py` is a compatibility wrapper over the app factory in `src/app/app_factory.py`.
Defaults: `API_HOST=192.168.100.136`, `API_PORT=5001`.

### API dev mode

```bash
uv run python src/API.py --dev --host 127.0.0.1 --port 5001
```

You can also enable dev mode with `API_DEBUG=1`.

## Run workflow CLI

```bash
uv run python src/scripts/workflow_cli.py -h
```

`main.py` is a wrapper that forwards to this CLI.

## Run tests

```bash
uv run pytest -q
```

## Strict type checking

```bash
uv run pyright
```

Current strict pyright config is in `pyrightconfig.json`.

### Strict scope

- `src/app`
- `src/services`
- `src/pipelines`
- `src/integrations`
- `src/infrastructure`
- `src/workflows`

### Explicit exclusions

- `src/util`
- `src/angles`
- `src/**/__pycache__`

## Optional env vars

Some endpoints/pipelines require provider credentials (for example):

- `OPENAI_API_KEY`
- `GOOGLE_PALM_API_KEY`
- `GROQ_API_KEY`
- `LM_STUDIO_URL`
- `LM_STUDIO_API_TOKEN`
- `GOOGLE_CSE_ID`
- `GOOGLE_API_KEY_1`..`GOOGLE_API_KEY_5`
- `SCRAPINGDOG_API_KEY`
- `OLLAMA_API_KEY`
- `NEWS_API_KEY`

Use a local `.env` file (loaded by `python-dotenv`) for development.
