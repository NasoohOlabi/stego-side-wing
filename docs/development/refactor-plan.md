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
| 2.3 Kill the import-time runner singleton | 🚧 blocked — investigated further: 8 `test_api_v1_*.py` files patch methods directly on the shared instance (`monkeypatch.setattr(api_v1_routes.runner, ...)`), which works today only because `routes_tools.py`/`routes_workflows.py` import the same `runner` object by reference. Full removal needs route-layer dependency injection (fetching the runner via `current_app`/`g` instead of a module-level import) across both route files plus migrating all 8 test files — a large, separate change from moving `mkdir`. Also found a landmine for the smaller half: `workflows/adapters/content.py::_cache_content` writes to `get_config().url_cache_dir` with no defensive `mkdir` of its own, wrapped in a bare `except Exception: pass` — removing `WorkflowConfig.__init__`'s eager mkdir without adding a guard there would silently break URL caching. Recorded in baseline; no code changed. |
| 2.4 Add constructor injection with `None`-defaults | ✅ |
| 2.5 Add `import-linter` as an automated layering guard, wire into CI | ✅ — 3 contracts, gated between ruff and pyright |

## Phase 3 — Split the god objects

| Step | Status |
|---|---|
| 3.1 Deduplicate `encode` and `encode_binary_selection_bits` before splitting either | ✅ — `encode` 444 → 368 lines, `encode_binary_selection_bits` 244 → 195; extracted `_generate_candidate_groups`, `_decoded_indices`, `_encode_success_result`, `_candidate_validation_audit`, `_diagnostic_bits_fields`, `_encode_failure_result`, `_encode_exception_result`, `_sharpen_until_accepted` |
| 3.2 Split `stego.py` (2342 L) into a package | 🟡 — six pure-function clusters extracted into siblings (`stego_comment_tree.py`, `stego_extractive.py`, `stego_contextuality.py`, `stego_anchor_text.py`, `stego_audit.py`, `stego_results.py`), all re-exported at the old `_name` form in `stego.py` for the tests that access them as `stego.<name>` or import a few directly. `stego.py`: 2211 → 1874 lines. `StegoPipeline`'s two remaining oversized clusters — the candidate generate/evaluate/sharpen core (~330 L, tightly coupled to `self.llm`/`self.decode`, already investigated once as not mergeable into one shared loop — see 3.1) and multi-frame planning (~215 L) — are not split; either needs a real design decision (explicit collaborator params or a small class), not a pure relocation like the six that landed. |
| 3.3 Split `runner.py` (2249 L) into a package, finishing the stalled `runner_orchestration_utils.py` extraction | 🟡 — **not a package**: ~10 test files patch `workflows.runner.<name>` directly by dotted path (e.g. `test_runner_live_sim.py`), which a package move would break. Decomposed the single largest method instead, in place: `run_prep_until_google_quota_then_stego` (332 L) → ~40 L of phase orchestration plus 12 named-phase private methods, verified behavior-identical by capturing and diffing the full progress-event stream across 4 scenarios (byte-identical, including two paths — `failed_post_without_id`, `repeat_failed_post` — that had no prior test coverage; now pinned in `test_runner_prep_quota_stego.py`). `run_full_pipeline`'s verbatim-duplicate tail also collapsed (136 → 50 lines, part of 3.4's landing). Five more oversized methods remain undecomposed: `run_double_process_new_post` (251 L), `validate_post_pipeline` (231 L), `run_batch_angles_determinism` (215 L), `run_stego` (133 L), `run_stego_receiver_live_sim` (99 L) — same treatment applies, not yet done. |
| 3.4 Collapse the 5-fold `data_load → research → gen_angles` sequence into one `run_context_stages(...)` helper | 🟡 — the single `run_context_stages(...)` was investigated and rejected: the five sites run the same stages through three different pipeline APIs (`preview_post` / `process_post_id` / `process_post_objects`) with different failure policies (receiver *raises*, prep breaks on quota, full-pipeline returns early) and different emit contracts. The two real duplications under it were collapsed instead — `workflows/stages.py` now single-sources the stage↔step triple that was hand-spelled in 4 places, and `run_full_pipeline`'s verbatim duplicate tail became two helpers (136 → 50 lines). See baseline for both writeups. |
| 3.5 Replace the LLM provider if/elif with a strategy map (`workflows/adapters/providers/`) | 🟡 — **not a package**: ~15 test sites monkeypatch module-level names in `workflows.adapters.llm` directly (`.requests.post`, `.genai.Client`, `._genai_generate_text`), which a `providers/` sub-package would break (a re-export shim breaks the moment a provider module captures a direct reference at import time; a circular re-import through `llm.py` is the only alternative, and not worth the risk). Did the part that's actually the defect: `call_llm`'s 4-branch if/elif is now a `_PROVIDER_CALLERS` dict lookup into the unchanged `_call_openai`/`_call_gemini`/`_call_groq`/`_call_lm_studio` methods. |
| 3.6 Thin out `wf_run`'s 13-branch if/elif into a command registry | ⛔ — investigated and rejected: each branch returns both a dispatch closure and an early-return error response, making a registry signature awkward for no real gain. The concrete defect the registry would have prevented (a listed command with no branch) is now caught directly by `test_api_v1_workflow_run_dispatch.py` — that test *was* the actual bug-fix (see Phase 5-era bug: `stego-receiver-live` silently ran the full pipeline). |

## Phase 4 — Typed contracts (Pydantic v2)

| Step | Status |
|---|---|
| 4.1 Convert `workflows/contracts.py` to Pydantic v2 | ✅ — 126 → 21 lines; 4 of 5 dataclasses were dead code (zero references anywhere), `to_dict`/`from_dict` were never called. The one live type, `FetchUrlResult`, is now a frozen `BaseModel` |
| 4.2 Model the `post_augmentation`/`sender_audit` dict contract (`PostAugmentation`/`SenderAudit` with explicit aliases) | 🟡 — **`TypedDict`, not `BaseModel`**: both dicts are mutated in place by `StegoPipeline.encode`/`encode_binary_selection_bits` across ~350 lines each (`sender_audit` gains ~10 fields after construction; `post_augmentation` gains `senderAudit`), which a frozen/validated model would force restructuring to support. `TypedDict` has zero runtime footprint but gives pyright a fixed key set to check ~90 call sites against — caught nothing new in production code, but two receiver-side runtime guards needed `pyright: ignore` comments explaining they're still load-bearing (the receiver reads this from arbitrary, possibly-foreign post JSON, so the type's guarantee doesn't hold there the way it does at the sender's construction site). |
| 4.3 Model the encoding profiles (`WORKFLOW_ENCODING_PROFILES`) | ✅ — `WorkflowEncodingProfileSettings`, a frozen Pydantic v2 `BaseModel`; unlike 4.2 this one *is* the stable/flat case (4 presets, 12 uniform fields, no mutation) a real `BaseModel` fits cleanly. The 12 string-keyed `_workflow_encoding_default(key)` call sites are unchanged — it reads via `getattr()` on the model now instead of dict indexing. |
| 4.4 Add `@validate_call` to the newly-extracted Phase 3 pure functions | 🟡 — applied to all 8 named functions, ran the full suite, found two independent real behavior breaks, reverted both categories: (1) `_generate_candidate_groups`/`_sharpen_until_accepted` take `llm_timings` as a mutate-by-reference output parameter, not a pure input — `validate_call` reconstructs a new validated list, so the caller stopped seeing the appends (`sender_audit["llm_timings"]` went permanently empty, caught by 3 tests); (2) the other four take `post_augmentation: PostAugmentation`, and the test suite's established convention of building deliberately partial `_augment_post` mocks is incompatible with strict required-field validation at that boundary. Landed on the two functions where it fit cleanly and matches every one of the 43 pre-existing `@validate_call` sites in shape: `_decoded_indices`, `_candidate_validation_audit` (no `PostAugmentation` param, no mutate-by-reference list). |
| Bug fix: `natural_sharpened` prompt style unreachable (missing from the config `Literal`) | ✅ — fixed and flagged for review per the plan's own instruction (behavior change, landed as a separate commit) |

## Phase 5 — Errors and configuration

| Step | Status |
|---|---|
| 5.1 Introduce an exception hierarchy (`workflows/errors.py`) | ✅ — `WorkflowError` + `NoUnprocessedPostsError`, `QuotaExceededError`, `DataLoadFetchError`, `ReceiverDataLoadError`, sitting behind the existing message-based detectors (not yet replacing them — see 5.2) |
| 5.2 Tighten the bare `except Exception` in `DecodePipeline.decode` (returns `None` on any failure, indistinguishable from "no match") | ✅ — every designed no-match outcome already returned `None` from its own point in the method with its own warning log; the outer bare except was reachable only for something `decode()` did not anticipate. Now raises `DecodeUnexpectedError(WorkflowError, RuntimeError)` instead. Checked all 3 callers first: `StegoPipeline._decode_candidate` propagates into `encode()`'s existing outer exception handler (a real improvement — a bug used to burn the whole retry budget looking like "didn't decode" before finally reporting an unrelated "Decoding validation failed"); `ReceiverPipeline.decode_payload` already raised a generic `RuntimeError` uncaught for *any* `None`, so this just makes that propagated exception more specific; `WorkflowRunner.run_decode` had no try/except and still doesn't need one. |
| 5.3 Map errors to HTTP status in one place | ✅ — 14 duplicated `except Exception: return fail(..., 500)` handlers replaced by one `workflow_error_response()` keyed on the Phase 5.1 hierarchy (409/503/502/500) |
| 5.4 Give `infrastructure/config.py` (781 L, 50 getters) a `pydantic-settings` model | ⛔ — investigated and rejected: nearly every getter re-reads `os.environ`/`.env` live on **every call** (confirmed by grep: 30+ `monkeypatch.setenv(...)` sites across `test_workflow_capacity_config.py` alone call a getter immediately afterward with no re-instantiation step), which is exactly what `pydantic-settings`' idiomatic singleton-instantiated-once `BaseSettings` pattern does not support without re-instantiating on every access — at which point it stops being simpler than what exists. Several getters also distinguish process-env-only from `.env`-fallback reads (`_workflow_env_raw`), a per-field distinction `BaseSettings`' default env-loading doesn't make cleanly. No code changed. |
| 5.5 Fix the two CWD-dependent relative paths (`kv_service.DB_FILE`, `app_factory.py` `CACHE_DIR`) | ✅ — both now resolve through `REPO_ROOT` |

## Phase 6 — Guardrails and docs

| Step | Status |
|---|---|
| 6.1 Ratchet the linter (add `SIM`, `C4`, `RUF`, `PT`) | ✅ — no carve-outs needed |
| 6.2 Bring `scripts/` under pyright | ✅ — `scripts` added to `include` with its own `executionEnvironment`; all 25 measured errors fixed. `src/tests` was measured too (206 errors, 182 of them the monkeypatch idiom) and deliberately excluded — see baseline for the full writeup and the `executionEnvironments` scoping trap |
| 6.3 Cover the gaps this refactor depends on (`integrations/`, `workflow_facade.py`, etc.) | 🟡 — `workflow_facade.py` has tests (added in 2.2). Added `test_news_api.py` for `integrations/news_api.py` (6 tests, was zero coverage); writing it surfaced a real pre-existing bug (see baseline). `integrations/duckduckgo_api.py` (async/`aiohttp`, no `pytest-asyncio`/`anyio` dependency or async test precedent anywhere in this suite — adding one is a bigger, separate decision) and `integrations/scrapingdog_api.py` (also has an unrelated wart: writes `last_response_from_sdg.json` to the CWD unconditionally) remain untested. Several services also still have none. |
| 6.4 Fix doc drift (`AGENTS.md` pyright scope, `refactor-summary.md` stale tree, `architecture-layers.md` port direction) | ✅ — `refactor-summary.md` marked historical and pointed at accurate docs; `architecture-layers.md` documents the Phase 2 port/adapter direction |

---

## What's genuinely left

Every phase has landed in some form; three items remain partial and one is fully blocked:

1. **3.2** — `StegoPipeline`'s candidate generate/evaluate/sharpen core (~330 L) and
   multi-frame planning (~215 L) are still inside the class. Six pure-function clusters
   already moved out; these two need a real design decision (explicit `llm`/`decode`
   params, or a small collaborator class), not a pure relocation.
2. **3.3** — five oversized `WorkflowRunner` methods remain undecomposed:
   `run_double_process_new_post` (251 L), `validate_post_pipeline` (231 L),
   `run_batch_angles_determinism` (215 L), `run_stego` (133 L),
   `run_stego_receiver_live_sim` (99 L). The pattern is proven
   (`run_prep_until_google_quota_then_stego`, 332 → ~40 L, verified via an event-stream
   diff) — same treatment, not yet applied to the rest.
3. **6.3** — `integrations/duckduckgo_api.py` and `scrapingdog_api.py`, and several
   services, still have no tests.

Blocked:

- **2.3** (kill the import-time runner singleton) needs route-layer dependency injection
  across `routes_tools.py`/`routes_workflows.py` plus migrating 8 test files off
  instance-patching — investigated in more depth than before; still a large, separate
  change. A landmine was found for the smaller "move `mkdir` out of `__init__`" half too:
  see baseline.

Rejected as literally specified, with a smaller safe equivalent landed instead where one
existed: **3.3/3.5's "package"** (test monkeypatch seams), **4.2's `BaseModel`**
(mutation-heavy construction), **5.4's `pydantic-settings`** (incompatible with the live
env-reload test pattern, no equivalent landed — see baseline).

Every change above landed independently with the full gate green (ruff, ruff format,
import-linter, pyright, pytest). See [`refactor-baseline.md`](refactor-baseline.md) for the
gate command, the phase-by-phase test/coverage trajectory, all bugs found along the way,
and the refactors that were investigated and explicitly rejected.
