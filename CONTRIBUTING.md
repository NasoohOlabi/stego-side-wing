# Contributing

## Entrypoints

| Purpose | Command / path |
|--------|------------------|
| HTTP API | `uv run python src/API.py` (see `src/app/app_factory.py`) |
| Operator HTTP contract | [docs/api-spec.md](docs/api-spec.md) |

`src` is on `PYTHONPATH` for normal `uv run` usage (see [AGENTS.md](AGENTS.md)).

## Quality checks

```bash
uv sync --all-groups
uv run pytest
uv run pyright
uv run ruff check src/app/routes/api_v1 src/app/schemas
uv run ruff format src
```

## Optional extras

- **Metrics (perplexity via torch):** `uv sync --extra metrics` (see README).

## API v1 layout

Versioned routes live under [`src/app/routes/api_v1/`](src/app/routes/api_v1/). [`src/app/routes/api_v1_routes.py`](src/app/routes/api_v1_routes.py) re-exports `bp`, `runner`, and symbols tests may monkeypatch.
