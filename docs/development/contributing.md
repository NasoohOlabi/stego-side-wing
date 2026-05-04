# Contributing

## Entrypoints

| Purpose | Command / path |
|--------|----------------|
| HTTP API | `uv run python src/API.py` (see `src/app/app_factory.py`) |
| Operator HTTP contract | [api-spec.md](../reference/api-spec.md) |

`src` is on `PYTHONPATH` for normal `uv run` usage. See the root `AGENTS.md` for agent rules and repo conventions.

## LLM Prompt Changes

Workflow/system LLM prompt text is a guarded area. Do not edit prompt text in `config/workflow_llm_prompts.json`, `src/workflows/utils/workflow_llm_prompts.py`, or related prompt files without double-checking with the project owner first.

## Local/Dev Security Scope

This API is primarily local/dev-only. Treat missing authentication on `/api/v1/state/*`, prompt admin routes, and similar maintenance endpoints as expected in local runs unless deployment scope changes.
If the service is exposed outside localhost (shared machine, LAN, reverse proxy, or internet-facing host), re-evaluate auth/authorization as a priority issue.

## Prompt Encoding Hygiene

Stego quality depends on stable visible text and predictable tokenization.
Avoid zero-width or non-rendering Unicode carriers and avoid mojibake/corrupted prompt characters (for example `â€”`, `â€œ`, `âŒ`).
Encoding corruption can significantly reduce sender/receiver recoverability and decoding accuracy.

## Quality Checks

```bash
uv sync --all-groups
uv run pytest
uv run pyright
uv run ruff check src/app src/services src/content_acquisition src/integrations src/infrastructure src/workflows
uv run ruff format --check src/app src/services src/content_acquisition src/integrations src/infrastructure src/workflows
```
