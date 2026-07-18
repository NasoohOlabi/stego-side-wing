# Live Run Authorization — Staged Plan

Authorized by the user on 2026-07-18 after independent verification of the Phase 0–2 claims
(clean trees, commits `3b1cacb`–`a8151f5` and `ae520f7`, 46 prepared angle artifacts,
deterministic preflight `post_ids_sha256` `e44bb13f…` reproduced with only `created_at_utc`
differing between dry runs).

## Scope of authorization

Chargeable live calls are authorized **only** for the stages below, in order, each gated on
the previous stage's gate. Authorization covers:

- Search/fetch provider usage (ScrapingDog, NewsAPI/LumenFeed, Jina, Google CSE) for the
  remaining 54 post preparations, including retries for candidates that fail the
  two-tangent gate.
- Generation, judge, and paraphrase calls for the staged 5/25/100 benchmark runs and the
  `max_capacity` lane.

## Standing constraints (apply to every stage)

1. `WORKFLOW_LLM_BACKEND=lm_studio` stays as-is. Switching any workflow/pipeline call to a
   paid per-token backend (AI Studio, OpenAI, OpenRouter, …) requires stopping and asking
   the user first — that is a new charge class, not covered here.
2. All operating rules in `README.md` remain in force: no `--allow-dirty` for publication
   runs, no mock endpoint presented as the official ZLG baseline, forbidden-carrier and
   prompt red-line rules, `.venv\Scripts\python.exe -m pytest/-m pyright` on this machine.
3. Commit this file (and any related doc changes) before running — the runner refuses a
   dirty tree.
4. Log each stage's outcome in `PROGRESS.md` before starting the next stage.

## Stage A — Complete the frozen corpus (54 live preparations)

- Run `uv run python scripts/prepare_publication_posts.py --count 100 --allow-live`.
- Expected charges: search/fetch APIs only (~a handful of calls per post; LLM angle
  generation is local via LM Studio backend).
- Then freeze the 100-post manifest and run `benchmark_preflight` **twice**; the manifest
  must reproduce exactly (timestamp-only differences allowed, as with the 46-post subset).

**Gate:** 100 eligible posts; manifest validates; preflight deterministic across two runs.
**Report before Stage B:** cumulative search-API call counts per provider, and how many
candidate posts were consumed (including failures) to reach 100.

## Stage B — 5-post infrastructure smoke test

- Full paired run on 5 posts against the deployed ZLG service (`ae520f7`) and local judge.

**Gate:** every (post, method) tuple has an attempt row; accounting invariants pass; no
infrastructure errors (service restarts, timeouts, framing mismatches).

## Stage C — 25-post pilot

- `--stage pilot`; inspect retained failures manually.

**Gate:** automated gate passes — ≥80% generation acceptance and ≥95% verified recovery per
method. **If the gate fails, stop and report to the user; do not proceed to Stage D.**

## Stage D — Full 100-post run + max_capacity lane

- Expand via `--stage full` / `auto` only after Stage C's gate passes.
- Run the `max_capacity` lane separately with the same ceilings; never mix its claims with
  the capacity-matched lane.

**Gate:** complete attempt accounting per execution-plan Phase 3.

## Stage E — Post-hoc evaluation (offline, no new authorization needed)

- Phase 4 analyses (passive detector, suspiciousness judging, robustness attacks,
  multi-post legacy/v1 naturalness lanes) run from cached artifacts. New live calls beyond
  the frozen corpus are **not** covered by this authorization.

## Stop-and-report conditions

Stop immediately and report to the user if any of these occur:

- Any stage gate fails.
- A provider starts rejecting calls (quota/billing) or per-call cost is materially higher
  than the search-only expectation above.
- Round-trip extraction against the ZLG service starts failing in a way that requires
  changing backend, model, or EGS parameters (that invalidates the frozen protocol).
