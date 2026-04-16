# stego-side-wing

Python backend and workflow runtime for the stego pipelines.

Contributor notes: **[CONTRIBUTING.md](CONTRIBUTING.md)**.

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
Defaults: `API_HOST=127.0.0.1`, `API_PORT=5001` (overridable with env vars or `--host` / `--port`).

### API dev mode

```bash
uv run python src/API.py --dev --host 127.0.0.1 --port 5001
```

You can also enable dev mode with `API_DEBUG=1`.

HTTP contract for `/api/v1/*` (workflows, tools, metrics, state): **[docs/api-spec.md](docs/api-spec.md)**. Workflow LLM templates live in `config/workflow_llm_prompts.json` and are exposed at `GET` / `PUT` / `POST …/reset` under `/api/v1/prompts/workflow-llm` (see **Concepts → Workflow LLM prompts** and **State** in that doc).

## Metrics (perplexity, KL/JSD)

- **Reports directory:** `<repo>/metrics` — timestamped JSON files from perplexity and divergence runs.
- **CLI (repo root):** `uv run python scripts/avg_perplexity.py` and `uv run python scripts/avg_kld.py` (`-h` for options). Defaults write under `metrics/`.
- **API:** `POST /api/v1/tools/metrics/perplexity`, `POST /api/v1/tools/metrics/divergence`, `GET /api/v1/tools/metrics/history` — see **[docs/api-spec.md](docs/api-spec.md)** (Tools → metrics). `GET /api/v1/state/paths` includes `metrics.dir`.
- **Note:** Perplexity evaluation needs `torch` and `transformers` — install with `uv sync --extra metrics` (see `pyproject.toml` `[project.optional-dependencies]`; divergence does not need them).

## Run tests

```bash
uv run pytest
```

## Lint and format

```bash
uv run ruff check src/app/routes/api_v1 src/app/schemas
uv run ruff format src
```

## Strict type checking

```bash
uv run pyright
```

Current strict pyright config is in `pyrightconfig.json`.

### Strict scope

- `src/app`
- `src/services`
- `src/content_acquisition`
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
- `GOOGLE_PALM_API_KEY` (Generative Language API / AI Studio; alias: `GOOGLE_AI_API_KEY` if `GOOGLE_PALM_API_KEY` is unset)
- `GROQ_API_KEY`
- `WORKFLOW_LLM_BACKEND` — `ai_studio` (default; aliases `google` / `gemini`) or `lm_studio` for workflow pipelines (`LLMAdapter` → Google `generateContent` when not `lm_studio`)
- `GOOGLE_AI_STUDIO_MODEL` — model id when using Google backend (default `gemma-4-26b-a4b-it`)
- `LM_STUDIO_URL`
- `LM_STUDIO_API_TOKEN`
- `GOOGLE_CSE_ID`
- `GOOGLE_API_KEY_1`..`GOOGLE_API_KEY_5`
- `SCRAPINGDOG_API_KEY`
- `OLLAMA_API_KEY`
- `NEWS_API_KEY`
- `DOUBLE_PROCESS_VALIDATION_ROOT` — optional base directory for double-process dedicated caches: `pass_1/` and `pass_2/` each contain their own `url_cache/`, `angles_cache/`, and `research_terms_cache.db` (default: `datasets/double_process_validation` under the repo)

Use a local `.env` file (loaded by `python-dotenv`) for development.