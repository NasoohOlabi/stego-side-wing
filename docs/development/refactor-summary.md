# Refactor Summary

The maintainability refactor is complete.

## Delivered

- Established a clean test, lint, type-check, formatting, and import-layer baseline.
- Removed dead code and consolidated duplicated utilities, schemas, metrics, and workflow helpers.
- Enforced application, service, workflow, integration, and infrastructure boundaries with Import Linter.
- Replaced the import-time workflow runner singleton with Flask application-owned dependency injection.
- Moved URL-cache directory creation to the content adapter's write boundary.
- Decomposed the oversized `WorkflowRunner` orchestration methods into focused helpers and collaborators.
- Extracted `StegoPipeline` multi-frame planning and candidate generation/evaluation/sharpening into focused modules.
- Added typed workflow contracts, encoding profiles, and a workflow exception hierarchy.
- Centralized workflow HTTP error mapping and corrected repository-root-relative paths.
- Replaced the LLM provider dispatch branch chain with a strategy map while preserving monkeypatch seams.
- Expanded direct coverage across workflow facade, external search integrations, and previously uncovered services.
- Brought `scripts/` under Pyright and strengthened Ruff rules.

Some originally proposed implementation shapes were deliberately replaced by safer equivalents:

- `runner.py` and the LLM adapter retained stable module paths because tests and integrations patch those paths.
- Mutation-heavy sender contracts use `TypedDict` rather than runtime `BaseModel` objects.
- Live environment-reading configuration getters remain functions rather than a cached `BaseSettings` singleton.
- Heterogeneous workflow command and context-stage branches remain explicit where a registry would obscure their different contracts.

## Validation

The completed refactor passes:

- Ruff lint
- Ruff formatting
- Pyright with zero errors and warnings
- All three Import Linter architecture contracts
- The complete pytest suite (one intentional skip)
- `git diff --check`

Current architecture details live in
[`architecture-layers.md`](architecture-layers.md), and routine validation guidance lives
in [`validation-per-phase.md`](validation-per-phase.md).
