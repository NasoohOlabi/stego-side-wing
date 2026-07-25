# Maintainability refactor — measured baseline

Reference point for the phased maintainability refactor. Captured on branch
`refactor/maintainability-phase-0` at commit `1f9d773` (parent of the first
behavioral change). Every phase must hold or improve these numbers.

For the phase-by-phase plan and a status marker on every step (done / partial / decided
against / not started / blocked), see [`refactor-plan.md`](refactor-plan.md). This document
is the evidence behind those statuses — the gate numbers, the bugs found, and the
refactors that were investigated and rejected.

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
| 3 — god objects (partial) | 558 | 69% | `encode` 444 → 368 lines, `encode_binary_selection_bits` 244 → 195; `stego.py` now 88% covered |
| 5 — errors (partial) | 574 | 69% | typed `workflows/errors.py` behind the existing message-based detectors |
| 6 — guardrails (partial) | 589 | 71% | ruff ratcheted to SIM/C4/RUF/PT with no carve-outs; `/workflows/run` dispatch bug found and fixed |
| 4.1 — contracts.py to Pydantic v2 | 601 | 71% | `workflows/contracts.py` 126 → 21 lines: 4 of 5 dataclasses were dead code (zero references), `to_dict`/`from_dict` were never called anywhere. The one live type, `FetchUrlResult`, is now a frozen `BaseModel` |
| 5.3 — HTTP error mapping | 601 | 71% | 14 duplicated `except Exception: return fail(..., 500)` handlers in `routes_workflows.py` replaced by one `workflow_error_response()` keyed on the Phase 5.1 exception hierarchy (409/503/502/500) |
| 6.2 — pyright on `scripts/` | 601 | 71% | `scripts` added to pyright `include` with its own `executionEnvironment`; all 25 measured errors fixed (see below) |

### Still open

- **Phase 3.2–3.4** — splitting `stego.py` and `runner.py` into packages.
- **Phase 3.5** — LLM provider strategy. Note the four `_call_*` methods are less alike than
  they look: the Gemini one rotates API keys and falls back from SDK to REST.
- **Phase 5.2** — the bare `except` wrapping `DecodePipeline.decode` returns `None`, making a
  crash indistinguishable from "no match" *to the caller* (it is logged with a traceback).
  Fixing it properly means changing the return contract, which ripples through
  `_evaluate_candidate_groups` and the receiver — Phase 4-scale work, not a local edit.
- **Phase 5.4** — a validated settings model for `infrastructure/config.py` (781 lines,
  ~75 getters).
- **Phase 6.3** — `integrations/` and several services still have no tests.
- **Phase 2.3** — the import-time runner singleton, blocked as described above.

### Phase 6.2 — done: pyright now covers `scripts/`

`scripts` was added to pyright's `include` with its own `executionEnvironment` entry
(`root: "scripts"`, `extraPaths: ["src"]`), and `src/tests` was moved to `exclude`. Trap
recorded so nobody re-hits it: adding `scripts` to `include` **without** giving it its own
`executionEnvironments` entry silently widens the checked set to `src/tests` as well,
because the existing environment is rooted at `src` — that combination measured 203 errors
instead of 25.

`src/tests` itself stays excluded: checking it measured 206 errors, 182 of them
`reportAttributeAccessIssue` from the monkeypatch idiom (assigning lambdas onto methods of
`__new__`-built objects). Enforcing that would fight the established test style rather than
find bugs, so it was left out deliberately.

All 25 measured `scripts/` errors are now fixed:

- **13 `reportOptionalMemberAccess`**, all the same shape:
  `x.get(k) if isinstance(x.get(k), dict) else {}` calls `.get` twice and narrows nothing,
  because the `isinstance` check tests a different expression than the one whose value is
  used — every downstream `.get` on the result was an access on a possibly-`None` value.
  Fixed by extracting `infrastructure/mappings.py` (`dict_field`, `list_field`) and using it
  at all 16 occurrences of the idiom across `scripts/` and `src/services/`.
- **6 `reportPrivateUsage`** — several scripts import a leading-underscore helper from a
  sibling script or from `StegoPipeline` (`_post_json`, `_run_profile`,
  `_select_post_ids`, `_has_usable_angles`, `_read_json`,
  `_load_default_payload_and_tag`). These are genuine cross-module call sites, not internals
  leaking by accident, so each was renamed to drop the underscore at its definition and every
  call site (including the `monkeypatch.setattr` string-name targets in the matching tests).
- **1 `reportMissingImports`** — `scripts/make_schema.py` imports `genson`, which was never
  declared as a project dependency; the script could not run at all. Added to the `dev`
  dependency group.
- **1 `reportCallIssue`** — `scripts/download_qwen_zlg_model.py` passed
  `resume_download=True` to `huggingface_hub.snapshot_download`; that parameter was removed
  in `huggingface_hub` 1.x (downloads resume from cache automatically now). Dropped it.
- **1 `reportAttributeAccessIssue`** — `run_actual_workload_e2e.py` set a
  `.feedback_envelope` attribute directly on a caught `Exception` instance to carry a payload
  up to the caller. `Exception` has no such attribute typed; used `setattr()` for the
  intentional dynamic-attribute pattern.
- **1 `reportArgumentType`**, **1 `reportMissingParameterType`** — a `dict[str, Any]` that
  pyright inferred too narrowly from its first literal assignment, and a bare-annotation
  parameter in `make_schema.py`. Both are local type-annotation fixes with no behavior change.

While measuring this (before any fixes), pyright also caught that
`scripts/run_zlg_comparison.py` was dead on arrival: it constructed
`ComparisonInput(domain=..., comment_chain=...)` after those fields had been replaced by
`cover_texts`, so it raised `TypeError` immediately, and it lacked the `sys.path` bootstrap
its sibling scripts have so it could not even be imported. Both fixed in an earlier commit.

### Bugs found while refactoring

All found while refactoring, all fixed.

| Bug | Where |
|---|---|
| `config/pareto_variants.json` required by code but silently untracked (blanket `*.json` ignore) | `.gitignore` |
| `POST /workflows/run` with `command="stego-receiver-live"` ran `run_full_pipeline` while echoing the requested command | `routes_workflows.wf_run` |
| `natural_sharpened` prompt style implemented but absent from the config Literal and parser, so it could never be selected and both its branches were dead | `config.py` / `pipelines/stego.py` |
| KV store and Flask cache used bare relative paths, so which database you got depended on the process working directory | `services/kv_service.py`, `app/app_factory.py` |
| `scripts/run_zlg_comparison.py` was dead on arrival — built `ComparisonInput` with fields removed in an earlier refactor, and had no `sys.path` bootstrap so it could not import at all | `scripts/run_zlg_comparison.py` |
| Non-breaking hyphen inside a live search query string | `integrations/scrapingdog_api.py` |
| `infrastructure` imported `workflows` (bottom layer depending on an upper one) | `prep_run_manifest` |
| Env-precedence test passed only on machines without a `.env` | `test_angle_runner_llm_retries` |
| `genson` imported but never declared as a dependency — the script could not run in a clean env | `scripts/make_schema.py` |
| `huggingface_hub.snapshot_download(resume_download=True)` — parameter removed in `huggingface_hub` 1.x, script raised `TypeError` on any run | `scripts/download_qwen_zlg_model.py` |

### Phase 3 — where it stands

**Done.** A characterization suite around `StegoPipeline.encode` that fakes only the LLM,
backend and decode edges and lets everything else run — all ten arms of the retry loop are
pinned, including the exception handler, the no-samples early return and context-sharpen
acceptance. Then three deduplication increments, each green before the next, extracting:
`_generate_candidate_groups`, `_decoded_indices`, `_encode_success_result`,
`_candidate_validation_audit`, `_diagnostic_bits_fields`, `_encode_failure_result`,
`_encode_exception_result`, `_sharpen_until_accepted`.

**Deliberately not done: collapsing the retry loop into one shared implementation.**
The two paths differ in eight ways — augmentation source, audit seeding, whether context
sharpening runs, three distinct `timing_outcome` labels, two `error_details` shapes, and
five extra result fields. A loop parameterised on all of that reads worse than the two
methods do. The differences are also load-bearing: `"Decoding validation failed"` is
matched by `classify_failure` in `stego_feedback_service` and asserted in
`test_run_actual_workload_e2e`, so the error payloads cannot simply be merged.

The productive direction for these two methods is more named-phase extraction — pulling
out the remaining prep and per-attempt blocks — not a shared loop.

**Not started:** splitting `stego.py` and `runner.py` into packages (plan steps 3.2–3.4)
and the LLM provider strategy split (3.5).

For 3.5, note the four `_call_*` methods are *not* as similar as they look: the Gemini one
rotates through multiple API keys and falls back from the SDK to REST, while the others are
single-shot. A shared provider Protocol is still worth doing, but it is not a copy-paste
collapse. It is well covered — `test_llm_adapter_retries` (10) plus
`test_llm_redacted_thinking` (23).

For 3.6, `wf_run` was not converted to a registry. Doing so means moving 13 parameter-parsing
blocks into separate builders, and each returns both a dispatch closure and an early-return
error response, so the registry signature is awkward. The concrete defect the registry was
meant to prevent — a listed command with no branch — is now caught directly by
`test_api_v1_workflow_run_dispatch.py`, which is the safety the restructure was for.

**Phase 4.1 and 5.3 are done** (contracts converted to Pydantic v2; HTTP errors mapped in one
place — see the phase outcomes table above). **Phases 5.2, 5.4, and 3.2–3.5 remain open**,
each with the blocker recorded in "Still open" above.

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
