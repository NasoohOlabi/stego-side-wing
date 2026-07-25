# Maintainability refactor plan

This is the working plan for the maintainability refactor of `stego-side-wing`, checked
into the repo so the intent and status survive independently of any single tool's session
history. It was originally approved and executed via an external planning session; this
copy is the durable record.

**For the measured numbers behind each status below — test counts, coverage, exact bugs
found, rejected alternatives and why — see [`refactor-baseline.md`](refactor-baseline.md).**
This document tracks *what was planned and whether it happened*; that one tracks *the
evidence*.

Status legend: ✅ done · 🟡 partial · ⛔ decided against (with reasoning recorded) · ⬜ not
started · 🚧 blocked (blocker recorded)

## Context

`stego-side-wing` is the core Python backend for the zero-shot generative linguistic
steganography project. At the start of this refactor it had **28,086 LOC of non-test
`src/`** plus **9,924 LOC of `scripts/`**, and the structure had drifted past what the
repo's own rules allow:

- Two god objects held 16% of non-test `src/` — `workflows/pipelines/stego.py` (2342 L)
  and `workflows/runner.py` (2249 L). 236 of 1044 functions exceeded the repo's own 25-line
  limit.
- The documented layer direction (`app → services → workflows`) was inverted in practice —
  `workflows` imported `services` at 12 sites, 10 of them function-local specifically to
  hide the cycle.
- `services/workflow_facade.py` was a 30-line re-export with zero behavior.
- State traveled as untyped `dict[str, Any]` blobs despite a Pydantic v2 mandate; zero
  pipeline or adapter files used `@validate_call`.
- Error classification was done by substring-matching exception text; zero custom
  exception types existed in `src/`.

**Intended outcome:** the same observable behavior — identical HTTP responses, identical
run artifacts, identical codec output — reached through code a newcomer can navigate.

## Constraints (non-negotiable, held throughout)

1. **Behavior-preserving.** Pure extraction, renaming, and type-tightening.
2. **Prompt red line.** Never touch `config/workflow_llm_prompts.json`,
   `workflows/utils/workflow_llm_prompts.py`, or prompt text without explicit confirmation.
3. **Preserve monkeypatch seams.** When a symbol moves, keep a re-export at the old path.
4. **Sender/receiver framing.** Every change validated as "sender produces X → receiver
   recovers X".
5. **One concern per commit.**

---

## Phase 0 — Baseline and safety nets

| Step | Status |
|---|---|
| 0.1 Land a formatting-only commit (CI was red: 37 files failed `ruff format --check`) | ✅ |
| 0.2 Add `pytest-cov`, capture a coverage baseline | ✅ — 67% at baseline |
| 0.3 Add a round-trip characterization test (`test_stego_roundtrip_golden.py`) | ✅ |
| 0.4 Extract shared test fixtures into `conftest.py` (fakes + `no_network` guard) | ✅ |
| 0.5 Fix tooling traps (stale pyright excludes, `.gitignore` blanket `*.json`, doc drift) | ✅ |

## Phase 1 — Delete dead code and collapse duplication

| Step | Status |
|---|---|
| 1.1 Remove `src/util/` forked copies (kept re-export shims) | ✅ |
| 1.2 Delete dead seams and dead code (`_embed_in_comment_selection`, `_cross_validate`, `embed_invisible_payload`, the `write_debug_probe` scaffold) | ✅ |
| 1.3 Collapse verbatim duplicates (`_angle_signature`, `flatten_comments`, `_tokenize_content_words`/`_STOPWORDS`, `_text_preview`, `_flatten_angles`) | 🟡 — collapsed where safe; `_STOPWORDS`, `_text_preview`, and `_flatten_angles` were investigated and kept split (see "rejected refactors" in baseline — each pair differs in output, not just form) |
| 1.4 Collapse the duplicated LLM retry stack in `angle_runner.py` | 🟡 — shared transport-fault token sets extracted to `infrastructure/retry_policy.py`; the two retry loops themselves stay separate (independently tuned: 3 vs 6 attempts, different backoff, different 408 handling) |
| 1.5 Point `scripts/` at shared helpers (`infrastructure/cache.py`) | ⛔ — investigated and rejected: the cache helper swallows exceptions and returns `None`, scripts must fail loudly on bad input. Kept separate, recorded in baseline. (A different shared-helper win landed later in Phase 6.2: `infrastructure/mappings.py` for the `dict_field`/`list_field` idiom.) |

## Phase 2 — Fix layering and dependency injection

| Step | Status |
|---|---|
| 2.1 Break the `workflows → services` cycle via `workflows/ports.py` Protocols | ✅ — crossings 12 → 3 (the 3 remaining are documented, intentional exceptions in `.importlinter`) |
| 2.2 Make `workflow_facade.py` a real facade | ✅ |
| 2.3 Kill the import-time runner singleton | 🚧 blocked — ~10 `test_api_v1_*.py` files patch methods directly on the shared instance (`monkeypatch.setattr(api_v1_routes.runner, ...)`). Needs (a) moving `mkdir` out of `WorkflowConfig.__init__` and (b) migrating those tests onto constructor injection first. Blocker recorded in baseline. |
| 2.4 Add constructor injection with `None`-defaults | ✅ |
| 2.5 Add `import-linter` as an automated layering guard, wire into CI | ✅ — 3 contracts, gated between ruff and pyright |

## Phase 3 — Split the god objects

| Step | Status |
|---|---|
| 3.1 Deduplicate `encode` and `encode_binary_selection_bits` before splitting either | ✅ — `encode` 444 → 368 lines, `encode_binary_selection_bits` 244 → 195; extracted `_generate_candidate_groups`, `_decoded_indices`, `_encode_success_result`, `_candidate_validation_audit`, `_diagnostic_bits_fields`, `_encode_failure_result`, `_encode_exception_result`, `_sharpen_until_accepted` |
| 3.2 Split `stego.py` (2342 L) into a package | ⬜ not started |
| 3.3 Split `runner.py` (2249 L) into a package, finishing the stalled `runner_orchestration_utils.py` extraction | ⬜ not started |
| 3.4 Collapse the 5-fold `data_load → research → gen_angles` sequence into one `run_context_stages(...)` helper | ⬜ not started — flagged in the original plan as "the highest-value deduplication in the repo" (exact path where sender/receiver drift gets introduced) |
| 3.5 Replace the LLM provider if/elif with a strategy map (`workflows/adapters/providers/`) | ⬜ not started — verified in code: `llm.py` still dispatches `openai/gemini/groq/lm_studio` via if/elif to four `_call_*` methods, no `providers/` package exists. (A task-tracking label briefly marked this done in error; corrected.) |
| 3.6 Thin out `wf_run`'s 13-branch if/elif into a command registry | ⛔ — investigated and rejected: each branch returns both a dispatch closure and an early-return error response, making a registry signature awkward for no real gain. The concrete defect the registry would have prevented (a listed command with no branch) is now caught directly by `test_api_v1_workflow_run_dispatch.py` — that test *was* the actual bug-fix (see Phase 5-era bug: `stego-receiver-live` silently ran the full pipeline). |

## Phase 4 — Typed contracts (Pydantic v2)

| Step | Status |
|---|---|
| 4.1 Convert `workflows/contracts.py` to Pydantic v2 | ✅ — 126 → 21 lines; 4 of 5 dataclasses were dead code (zero references anywhere), `to_dict`/`from_dict` were never called. The one live type, `FetchUrlResult`, is now a frozen `BaseModel` |
| 4.2 Model the `post_augmentation`/`sender_audit` dict contract (`PostAugmentation`/`SenderAudit` with explicit aliases) | ⬜ not started |
| 4.3 Model the encoding profiles (`WORKFLOW_ENCODING_PROFILES`) | ⬜ not started — still a raw `dict[WorkflowEncodingProfile, dict[str, object]]` in `infrastructure/config.py` |
| 4.4 Add `@validate_call` to the newly-extracted Phase 3 pure functions | ⬜ not started as a targeted effort — `@validate_call` exists at 43 call sites repo-wide (pre-existing usage), but the Phase 3 extractions from step 3.1 were not specifically annotated |
| Bug fix: `natural_sharpened` prompt style unreachable (missing from the config `Literal`) | ✅ — fixed and flagged for review per the plan's own instruction (behavior change, landed as a separate commit) |

## Phase 5 — Errors and configuration

| Step | Status |
|---|---|
| 5.1 Introduce an exception hierarchy (`workflows/errors.py`) | ✅ — `WorkflowError` + `NoUnprocessedPostsError`, `QuotaExceededError`, `DataLoadFetchError`, `ReceiverDataLoadError`, sitting behind the existing message-based detectors (not yet replacing them — see 5.2) |
| 5.2 Tighten the bare `except Exception` in `DecodePipeline.decode` (returns `None` on any failure, indistinguishable from "no match") | 🚧 blocked — fixing it changes the return contract, which ripples into `_evaluate_candidate_groups` and the receiver; Phase 4-scale work (needs 4.2 typed contracts first), not a local edit |
| 5.3 Map errors to HTTP status in one place | ✅ — 14 duplicated `except Exception: return fail(..., 500)` handlers replaced by one `workflow_error_response()` keyed on the Phase 5.1 hierarchy (409/503/502/500) |
| 5.4 Give `infrastructure/config.py` (781 L, ~75 getters) a `pydantic-settings` model | ⬜ not started |
| 5.5 Fix the two CWD-dependent relative paths (`kv_service.DB_FILE`, `app_factory.py` `CACHE_DIR`) | ✅ — both now resolve through `REPO_ROOT` |

## Phase 6 — Guardrails and docs

| Step | Status |
|---|---|
| 6.1 Ratchet the linter (add `SIM`, `C4`, `RUF`, `PT`) | ✅ — no carve-outs needed |
| 6.2 Bring `scripts/` under pyright | ✅ — `scripts` added to `include` with its own `executionEnvironment`; all 25 measured errors fixed. `src/tests` was measured too (206 errors, 182 of them the monkeypatch idiom) and deliberately excluded — see baseline for the full writeup and the `executionEnvironments` scoping trap |
| 6.3 Cover the gaps this refactor depends on (`integrations/`, `workflow_facade.py`, etc.) | ⬜ not started — `workflow_facade.py` now has tests (added in 2.2); `integrations/` and several services still have none |
| 6.4 Fix doc drift (`AGENTS.md` pyright scope, `refactor-summary.md` stale tree, `architecture-layers.md` port direction) | ✅ — `refactor-summary.md` marked historical and pointed at accurate docs; `architecture-layers.md` documents the Phase 2 port/adapter direction |

---

## What's genuinely left

In priority order, the open, non-blocked items:

1. **3.4** — the 5-fold context-stage duplication (highest-value per the original plan; it's
   the exact sender/receiver-agreement path).
2. **3.2 / 3.3** — splitting the two god modules into packages.
3. **3.5** — the LLM provider strategy split.
4. **4.2 / 4.3 / 4.4** — the remaining typed-contract work (blocks 5.2).
5. **5.4** — settings model for `config.py`.
6. **6.3** — test coverage for `integrations/` and the untested services.

Blocked, in dependency order:

- **5.2** waits on **4.2** (needs a typed return contract to change `decode`'s bare-except
  behavior meaningfully).
- **2.3** waits on a `WorkflowConfig.__init__` change plus migrating ~10 test files off
  instance-patching.

Every phase above landed independently with the full gate green (ruff, ruff format,
import-linter, pyright, pytest). See [`refactor-baseline.md`](refactor-baseline.md) for the
gate command, the phase-by-phase test/coverage trajectory, all bugs found along the way,
and the refactors that were investigated and explicitly rejected.
