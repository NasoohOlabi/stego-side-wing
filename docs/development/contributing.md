# Contributing

## Entrypoints

| Purpose | Command / path |
|--------|----------------|
| HTTP API | `uv run python src/API.py` (see `src/app/app_factory.py`) |
| Operator HTTP contract | [api-spec.md](../reference/api-spec.md) |

`src` is on `PYTHONPATH` for normal `uv run` usage. See the root `AGENTS.md` for agent rules and repo conventions.

## Quality Checks

```bash
uv sync --all-groups
uv run pytest
uv run pyright
uv run ruff check src/app src/services src/content_acquisition src/integrations src/infrastructure src/workflows
uv run ruff format --check src/app src/services src/content_acquisition src/integrations src/infrastructure src/workflows
```
