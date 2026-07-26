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

| Gate | At baseline (commit `1f9d773`) | Current (after all landed phases) |
|---|---|---|
| `ruff check src scripts` | clean | clean |
| `ruff format --check src scripts` | clean (243 files) | clean |
| `pyright` | 0 errors, 0 warnings | 0 errors, 0 warnings |
| `import-linter` | n/a (added phase 2.5) | 3 contracts kept, 0 broken |
| `pytest -q` | 523 passed | 618 passed |
| Coverage (non-test `src/`) | 67% — 12,611 statements, 4,180 missed | 71% — 12,493 statements, 3,568 missed |

**Scope note — the gate is `src scripts`, not `.`.** Running bare `ruff check .` also picks
up `extreact_searched_values.py`, a tracked one-off script at the repo root, which fails with
9 findings (I001, E402, 2× UP015, 2× SIM114; 5 auto-fixable). Those are pre-existing and
outside every gate this refactor ran. Nothing else lives outside `src/` and `scripts/`. Left
alone deliberately: cleaning it is a separate concern from any refactor phase, and the file
looks like a superseded throwaway — worth deleting rather than linting, but that is a call
for the repo owner.

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

Largest modules — the refactor targets — at baseline vs. current:

| Baseline lines | Current lines | Path |
|---|---|---|
| 2342 | 1874 | `src/workflows/pipelines/stego.py` |
| 2249 | 2353 | `src/workflows/runner.py` |
| 1281 | 1287 | `src/workflows/utils/stego_codec.py` |
| 1189 | 1175 | `src/content_acquisition/angles/angle_runner.py` |
| 1146 | 1209 | `src/app/routes/api_v1/routes_workflows.py` |
| 1122 | 1050 | `src/workflows/adapters/llm.py` |

236 of 1044 functions exceeded the 25-line limit in `.cursor/rules/maintainability.mdc` at
baseline; not re-measured since (would need to re-run the same census script this number
came from).

**`runner.py` grew despite decomposition, and that's expected, not a regression.** Only
one of its six oversized methods was decomposed
(`run_prep_until_google_quota_then_stego`, plan step 3.3) — it went 332 lines → ~40 lines
of orchestration plus 12 new named-phase private methods (each with its own signature and
one-line docstring), a net addition of signature/docstring overhead across more, smaller
functions. Per-function quality improved (worst function 332 → ~70 lines) and two
previously-uncovered failure paths gained test coverage; the file total did not shrink
because the other five oversized methods (883 lines' worth, see refactor-plan.md 3.3) are
still undecomposed. `stego.py` shrank because six *entire* pure-function clusters moved
to sibling files rather than being decomposed in place.

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
| The five `data_load → research → gen_angles` sequences (plan step 3.4) | They run the same three stages through **three different pipeline APIs**: `preview_post` (receiver, non-persisting, returns `{post, report}`), `process_post_id` (double-process, per id), and `process_post_objects`/`process_posts` (batch). Failure policy differs too — the prep loop breaks on a Google quota error, `run_full_pipeline` returns early on an empty batch, and `ReceiverPipeline.rebuild_context` **raises** on data-load failure because the receiver has no useful degraded mode. Progress emits differ in event name and payload at all five sites. One `run_context_stages(...)` would need flags for API family, per-stage kwargs, emit contract and failure policy — a worse read than the five sites, and it would put receiver failure semantics behind a parameter. What *was* genuinely shared is now in `workflows/stages.py`; see the note below. |
| scripts' `_read_json`/`_write_json` vs `infrastructure/cache.py` | `read_json_cache` is a **cache** helper: it swallows every exception and returns `None`. The scripts must fail loudly — several validate the payload is a dict and raise. Swapping them would let benchmark scripts silently proceed on unreadable input. The scripts' `_write_json` variants also disagree with each other on `ensure_ascii` (True vs False), which changes output bytes for non-ASCII text. |
| `llm.py`'s 4-provider dispatch as a `providers/` sub-package (plan step 3.5) | ~15 test sites monkeypatch module-level names directly: `workflows.adapters.llm.requests.post`, `.genai.Client`, `._genai_generate_text`. Moving those into sub-modules means either a re-export shim (breaks the moment a provider module captures a direct function reference at its own import time, since the patch only ever touches `llm.py`'s copy of the name) or a circular re-import through `llm.py` — real risk for a change meant to be behavior-preserving. Landed the actual defect instead: `call_llm`'s if/elif is a `_PROVIDER_CALLERS` dict lookup now; the four `_call_*` method bodies are untouched. |
| `PostAugmentation`/`SenderAudit` as Pydantic `BaseModel`s (plan step 4.2) | Both dicts are mutated in place across `StegoPipeline.encode`/`encode_binary_selection_bits` (~350 lines each) — `sender_audit` gains ~10 fields after construction (timings, candidate validation, diagnostic flags), `post_augmentation` gains `senderAudit`. A frozen or validated model would force restructuring both methods' control flow as a side effect of typing their contract, before either has its own decomposition pass (3.2, still open for the piece that touches this code). Used `TypedDict` instead — zero runtime footprint, but pyright now checks ~90 call sites against a fixed key set. |
| `@validate_call` on all 8 phase-3.1 extractions (plan step 4.4) | Applied to all 8, ran the full suite, found two real breaks: (1) `_generate_candidate_groups`/`_sharpen_until_accepted` take `llm_timings` as a mutate-by-reference output parameter — `validate_call` reconstructs a new validated list, so the caller stopped seeing appends (`sender_audit["llm_timings"]` went permanently empty; caught by `test_pipeline_stego_encode_characterization.py` and two context-sharpen tests). (2) The other four take `post_augmentation: PostAugmentation`, whose required fields are only guaranteed by real construction — the test suite's established convention of mocking `pipeline._augment_post` to return a deliberately partial dict (dozens of sites across `test_pipeline_stego.py` and friends) fails strict validation at that boundary. Kept on the two functions that match the shape of the other 43 pre-existing `@validate_call` sites: `_decoded_indices`, `_candidate_validation_audit`. |
| `infrastructure/config.py` as a `pydantic-settings` `BaseSettings` model (plan step 5.4) | Nearly every one of the 50 getters re-reads `os.environ`/`.env` live on **every call**, not once at construction — confirmed by grep: `test_workflow_capacity_config.py` alone has 30+ `monkeypatch.setenv(...)` sites that call a getter immediately afterward with no re-instantiation step anywhere in the suite. `pydantic-settings`' idiomatic pattern is a `BaseSettings` instance built once; supporting this repo's live-reload test pattern would mean re-instantiating on every access, at which point it is not simpler than what exists. Several getters also distinguish process-env-only reads from `.env`-fallback reads (`_workflow_env_raw`), a per-field distinction `BaseSettings`' default env-file loading does not make cleanly. No code changed. |

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

**Re-investigated, still blocked.** Confirmed the count precisely: 8 (not ~10)
`test_api_v1_*.py` files patch `api_v1_routes.runner`. Full removal needs the routes
themselves to stop importing `runner` as a module-level name and instead fetch it via
`current_app`/`g` — `routes_tools.py` and `routes_workflows.py` both do
`from app.routes.api_v1.runner_access import runner` and call `runner.<method>` directly
at dozens of call sites each, so this is route-layer dependency injection across two large
files, not a small patch.

Attempting only the smaller half (item 1 above, moving `mkdir` out of
`WorkflowConfig.__init__`) surfaced a real landmine: `workflows/adapters/content.py`'s
`_cache_content` writes to `get_config().url_cache_dir / f"{cache_key}.json"` with **no
defensive `mkdir` of its own**, and the write is wrapped in a bare `except Exception:
pass`. Removing the eager mkdir without adding a guard at that call site would make URL
content caching silently no-op forever — every write would raise `FileNotFoundError`,
swallowed invisibly. (The four other directories `WorkflowConfig.__init__` creates —
`posts_directory`/`url_fetched_dir`/`researched_dir`/`angles_dir`/`output_results_dir` via
`workflow_backend_client.save_post_local`, `research_terms_db_path` via
`gen_search_terms._init_cache_db`, `angles_cache_dir` via `angle_runner.py` — all already
have their own defensive `mkdir` at their real write sites, so only `url_cache_dir` is at
risk.) Not fixed: doing half of item 1 without item 2 does not unblock the plan's actual
goal (an injectable, non-import-time-constructed runner), so no code changed here this
pass.

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

**Not started:** splitting `stego.py` and `runner.py` into packages (plan steps 3.2–3.3)
and the LLM provider strategy split (3.5).

**3.4 — partly done, and the headline form rejected.** The plan called for collapsing the
five `data_load → research → gen_angles` sequences into one `run_context_stages(...)`. That
single helper is not worth building; the reasoning is in the rejected-refactors table above.
Two real duplications underneath it *were* collapsed:

- **The stage/step triple.** `data_load/research/gen_angles` ↔
  `filter-url-unresolved/filter-researched/angles-step` was re-spelled by hand in
  `WorkflowConfig.__init__`, the `validate_post` stage map, `build_prep_run_manifest`, and
  every `step=` literal in `runner.py`. `workflows/stages.py` is now the single source; all
  of them read from it. This is the drift the plan was actually worried about: a rename that
  lands on the sender side and not the receiver side breaks context agreement silently.
- **`run_full_pipeline`'s verbatim duplicate.** The "angle what we just researched, then
  finish" tail was byte-identical in the `filter-url-unresolved` and `filter-researched`
  branches, and the terminal `workflow_done` emit appeared five times. Now
  `_full_pipeline_angle_researched` and `_full_pipeline_done`; the function went 136 → 50
  lines. Same emit order, same payloads, same return values.

`_run_three_stage_post` (the per-post-id sequence) was already an extracted helper before
this phase and is unchanged.

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
