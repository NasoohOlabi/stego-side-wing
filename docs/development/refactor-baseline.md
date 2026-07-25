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

## Refactors that were investigated and rejected

Several apparent duplications turned out to be deliberate. They are recorded here so
nobody spends the effort again — and, more importantly, so nobody "fixes" them and
silently changes behaviour.

| Apparent duplicate | Why it stays split |
|---|---|
| `stego._STOPWORDS` vs `naturalness_gate._STOPWORDS` | Different sets (63 vs 33 entries; 32 words appear only in stego). They return different tokens for the same input and drive different decisions — contextuality scoring vs the naturalness gate. |
| `stego._text_preview` vs `protocol_utils.text_preview` | Different defaults (180 vs 160) and different truncation: one appends `...` after the cut so output can exceed the limit, the other reserves room inside it. They feed different output fields. |
| `stego_feedback_service._flatten_angles` vs `stego_codec.flatten_angle_groups` | The codec version injects a positional `idx` into every angle. The service must report angles unchanged. |
| `llm.py` vs `angle_runner.py` retry loops | Independently tuned: 3 vs 6 attempts, 1.0/30.0 s vs 1.5/60.0 s backoff, and `angle_runner` deliberately does not retry HTTP 408. Angle extraction sends far larger prompts. Only the transport-fault token sets were genuinely shared (now in `infrastructure/retry_policy.py`). |
| scripts' `_read_json`/`_write_json` vs `infrastructure/cache.py` | `read_json_cache` is a **cache** helper: it swallows every exception and returns `None`. The scripts must fail loudly — several validate the payload is a dict and raise. Swapping them would let benchmark scripts silently proceed on unreadable input. The scripts' `_write_json` variants also disagree with each other on `ensure_ascii` (True vs False), which changes output bytes for non-ASCII text. |

## Deferred: removing the import-time runner singleton

`app/routes/api_v1/runner_access.py` builds a `WorkflowRunner` at import. That is genuinely
undesirable — it constructs every pipeline and adapter, and `WorkflowConfig.__init__`
(`workflows/config.py:56-66`) creates eight directories, so *importing a route module writes
to disk*.

It is not removable without rewriting test mocks. Roughly ten `test_api_v1_*.py` files patch
**methods on the instance**:

```python
monkeypatch.setattr(api_v1_routes.runner, "run_receiver", _run)
```

That works only because `routes_workflows.py` holds the very same object (bound by
`from ... import runner` at line 24). Making the runner lazy, per-app, or proxied changes
which object the routes see, and every one of those tests breaks.

Two things must land before this is worth attempting:

1. Move directory creation out of `WorkflowConfig.__init__` so import stops touching the
   filesystem. Needs care: some readers assume the directories already exist, so a fresh
   clone could regress.
2. Migrate the affected tests onto the constructor injection added in phase 2.4, so they
   inject a runner instead of mutating a shared one.

Until then the singleton stays, and the import-linter contract keeps the layering honest
around it.

## Phase outcomes

| Phase | Tests | Coverage | Notes |
|---|---|---|---|
| Baseline | 523 | 67% | starting point |
| 0 — safety nets | 530 | 67% | golden characterization test, offline network guard, shared fakes |
| 1 — dead code & dupes | 530 | 68% | 307 statements removed; `src/util` forks, dead stego seams, the invisible-carrier write helper, and the f0bcc9 debug scaffold all deleted |
| 2 — layering & DI | 548 | 68% | `workflows -> services` crossings 12 → 3; ports + constructor injection added; `lint-imports` now gated in CI |
| 3 — god objects (in progress) | 555 | 69% | `encode` 444 → 396 lines, `encode_binary_selection_bits` 244 → 204; characterization tests added around `encode` first |

### Phase 3 — where it stands

Done: a characterization suite around `StegoPipeline.encode` that fakes only the LLM,
backend and decode edges and lets the rest run, then two deduplication increments
(`_generate_candidate_groups`, `_decoded_indices`, `_encode_success_result`,
`_candidate_validation_audit`, `_diagnostic_bits_fields`).

Next: the retry loop itself, still written twice. Parameterise it on the three real
differences the diagnostic path has — it skips context sharpening entirely, reports
`timing_outcome="diagnostic_success"`, and adds `_diagnostic_bits_fields` to its results.
Before attempting it, add characterization coverage for the three paths the current tests
do not reach: context-sharpen success, the exception handler, and the no-samples early
return.

Not started: splitting `stego.py` and `runner.py` into packages (plan steps 3.2–3.4), the
LLM provider strategy split (3.5), and the `wf_run` command registry (3.6).

### Gate added in phase 2

```bash
PYTHONPATH=src uv run lint-imports
```

Contracts live in `.importlinter` and encode `architecture-layers.md`, including the three
documented deferred-import exceptions. CI runs it between the ruff and pyright steps.

## Verifying a change is formatting-only

Compare parsed ASTs against HEAD rather than eyeballing the diff. Decode git blobs as
UTF-8 explicitly — `subprocess(text=True)` uses the Windows locale codec and will render
every non-ASCII character as mojibake, producing false differences.
