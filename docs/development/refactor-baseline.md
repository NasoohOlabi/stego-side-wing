# Maintainability refactor — measured baseline

Reference point for the phased maintainability refactor. Captured on branch
`refactor/maintainability-phase-0` at commit `1f9d773` (parent of the first
behavioral change). Every phase must hold or improve these numbers.

## Gates

Run from the repo root **in PowerShell** — `uv run pytest` fails under Git Bash with
`Failed to canonicalize script path`, which is why CI uses `python -m`:

```bash
uv run python -m ruff check src scripts; uv run python -m ruff format --check src scripts; uv run python -m pyright; uv run python -m pytest -q
```

| Gate | Baseline |
|---|---|
| `ruff check src scripts` | clean |
| `ruff format --check src scripts` | clean (243 files) |
| `pyright` | 0 errors, 0 warnings |
| `pytest -q` | 523 passed |
| Coverage (non-test `src/`) | **67%** — 12,611 statements, 4,180 missed |

Coverage is measured with the config in `pyproject.toml` (`[tool.coverage.run]`
omits `src/tests/*`):

```bash
uv run python -m pytest -q --cov --cov-report=term
```

## Size census

| Scope | Files | LOC |
|---|---|---|
| `src/**/*.py` | 193 | 39,458 |
| `src/` excluding tests | 107 | 28,086 |
| `src/tests/` | 86 | 11,372 |
| `scripts/*.py` | 50 | 9,924 |

Largest modules — the refactor targets:

| Lines | Path |
|---|---|
| 2342 | `src/workflows/pipelines/stego.py` |
| 2249 | `src/workflows/runner.py` |
| 1281 | `src/workflows/utils/stego_codec.py` |
| 1189 | `src/content_acquisition/angles/angle_runner.py` |
| 1146 | `src/app/routes/api_v1/routes_workflows.py` |
| 1122 | `src/workflows/adapters/llm.py` |

236 of 1044 functions exceed the 25-line limit in `.cursor/rules/maintainability.mdc`.

## Environment caveat

Two tests are sensitive to a developer `.env`, because `get_env`
(`src/infrastructure/config.py:39-44`) falls back to cached `.env` values that
`monkeypatch.delenv` cannot reach. CI has no `.env`, so this only bites locally. Tests
that assert on env precedence must use the `clear_llm_backend_env` /
`clear_workflow_capacity_env` fixtures in `src/tests/conftest.py`, which strip keys from
**both** `os.environ` and the dotenv cache.

## Verifying a change is formatting-only

Compare parsed ASTs against HEAD rather than eyeballing the diff. Decode git blobs as
UTF-8 explicitly — `subprocess(text=True)` uses the Windows locale codec and will render
every non-ASCII character as mojibake, producing false differences.
