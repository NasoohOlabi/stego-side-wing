# stego-side-wing

Python backend and workflow runtime for the stego pipelines.

Contributor notes: **[docs/development/contributing.md](docs/development/contributing.md)**.

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

HTTP contract for `/api/v1/*` (workflows, tools, metrics, state): **[docs/reference/api-spec.md](docs/reference/api-spec.md)**. Workflow LLM templates live in `config/workflow_llm_prompts.json` and are exposed at `GET` / `PUT` / `POST â€¦/reset` under `/api/v1/prompts/workflow-llm` (see **Concepts â†’ Workflow LLM prompts** and **State** in that doc).

## Metrics (perplexity, KL/JSD)

- **Reports directory:** `<repo>/metrics` â€” timestamped JSON files from perplexity and divergence runs.
- **CLI (repo root):** `uv run python scripts/avg_perplexity.py` and `uv run python scripts/avg_kld.py` (`-h` for options). Defaults write under `metrics/`.
- **API:** `POST /api/v1/tools/metrics/perplexity`, `POST /api/v1/tools/metrics/divergence`, `GET /api/v1/tools/metrics/history` â€” see **[docs/reference/api-spec.md](docs/reference/api-spec.md)** (Tools â†’ metrics). `GET /api/v1/state/paths` includes `metrics.dir`.
- **Note:** Perplexity evaluation needs `torch` and `transformers` â€” install with `uv sync --extra metrics` (see `pyproject.toml` `[project.optional-dependencies]`; divergence does not need them).

## Run tests

```bash
uv run pytest
```

## Testing, Pareto search, and sample generation

Runbook and artifact layout: [docs/results/workload-runs-and-artifacts.md](docs/results/workload-runs-and-artifacts.md).

The usual checks are:

- Targeted pytest for touched modules first.
- Full validation with `uv run pytest -q` for non-trivial edits.
- Strict type checks with `uv run pyright`.
- Pareto screening with `uv run python scripts/run_pareto_search.py`.
- Synthetic sample generation with `uv run python scripts/run_encoding_config_e2e.py`.
- Real sample generation with `uv run python scripts/run_actual_workload_e2e.py`.

How to view latest workload results quickly (PowerShell):

```powershell
# Latest progress log
$log = Get-ChildItem metrics/automation_logs -Filter 'pareto_security_retry_rating_*.progress.jsonl' |
  Sort-Object LastWriteTimeUtc -Descending |
  Select-Object -First 1
$log.FullName

# Tail progress/events
Get-Content $log.FullName -Tail 40

# Key run milestones and failures
Select-String -Path $log.FullName -Pattern 'actual_workload_run_complete|profile_complete|sample_failed'

# Latest run directory
Get-ChildItem metrics/e2e_runs -Directory |
  Sort-Object LastWriteTimeUtc -Descending |
  Select-Object -First 1 FullName, LastWriteTimeUtc
```

### Publication benchmark preflight

Freeze one post ID per line, then create a provenance manifest before any
confirmatory run:

```powershell
uv run python scripts/benchmark_preflight.py `
  --post-ids metrics/benchmark/post_ids.txt `
  --output metrics/benchmark/manifest.json
```

The command records the commit, dirty-tree state, model/protocol hashes, and
payload conditions. Dirty trees are refused by default; use
`--allow-dirty` only for exploratory runs.

## Lint and format

Ruff checks every maintained Python source file, including operational scripts:

```bash
uv run python -m ruff check src scripts
uv run python -m ruff format --check src scripts
```

To apply formatting: repeat the last command without `--check`.

Before opening a change, run the same checks as CI:

```bash
uv run python -m compileall -q src scripts
uv run python -m pyright
uv run python -m pytest -q
```

## Strict type checking

```bash
uv run python -m pyright
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
- `WORKFLOW_LLM_BACKEND` â€” `ai_studio` (default; aliases `google` / `gemini`) or `lm_studio` for workflow pipelines (`LLMAdapter` â†’ Google `generateContent` when not `lm_studio`)
- `GOOGLE_AI_STUDIO_MODEL` â€” model id when using Google backend (default `gemma-4-26b-a4b-it`)
- `LM_STUDIO_URL`
- `LM_STUDIO_API_TOKEN`
- `GOOGLE_CSE_ID`
- `GOOGLE_API_KEY_1`..`GOOGLE_API_KEY_5`
- `SCRAPINGDOG_API_KEY`
- `OLLAMA_API_KEY`
- `NEWS_API_KEY`
- `DOUBLE_PROCESS_VALIDATION_ROOT` â€” optional base directory for double-process dedicated caches: `pass_1/` and `pass_2/` each contain their own `url_cache/`, `angles_cache/`, and `research_terms_cache.db` (default: `datasets/double_process_validation` under the repo)

Use a local `.env` file (loaded by `python-dotenv`) for development.

