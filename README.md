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

Server defaults to `192.168.100.136:5001` (see `src/API.py`).

## Run tests

```bash
uv run pytest -q src/tests/test_parity.py
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

### Explicit exclusions (legacy/duplicate backlog)

- `src/util`
- `src/angles`
- `src/**/__pycache__`
- `src/ai_analyze.py`
- `src/headless_browser_analyzer.py`
- `src/scraper.py`
- `src/nest.py`

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
