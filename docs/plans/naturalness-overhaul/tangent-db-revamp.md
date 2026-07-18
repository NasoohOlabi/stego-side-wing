# Plan: Tangent-DB Revamp — Relevant *and* Distinct Tangents

Status: Phase 0 complete (report-only shadow); Phase 1 not activated
Owner: (you)
Related plans: [[prepared-posts-separate-persistence]], [[method-comparison-metrics-v2]]

## 1. Why

In the ZLG comparison (`zlg_demo_20260712_v6_200`) our generated comments read as
well-written and human-voiced, but a recurring failure is **topical drift**: a
fluent reply that does not belong in the thread (e.g. "competitive dynamics among
major coffee retailers" on a Trump-trial post; "global comparable store sales
growth" on a flood-deaths post). An LLM judge placing the comment among 9 real
replies catches exactly this — it is the bulk of our 23.7% sus-detection rate.

Root cause is in how the **tangent DB** (the flattened angle list, surfaced as
`angleEmbedding.TangentsDB`) is built. Today tangents are raw sentences pulled
from whatever entered the shared dictionary — including tangential **search-result
documents** — filtered only by a weak token-overlap gate and deduped only by exact
string match. So the DB can contain off-topic-but-distinct sentences (good for
capacity, bad for relevance) and near-duplicate sentences (bad for both).

This plan makes tangent selection **relevance-anchored** and **distinctness-aware**
without changing the sender/receiver bit contract.

## 2. How it works today (ground truth)

Trace, with files:

1. `src/workflows/utils/text_utils.py`
   - `build_post_text_dictionary_entries(post)` collects ordered entries from three
     sources: `post` (selftext), `search_results[*]`, and flattened `comments[*]`
     (lines 115–146).
   - `apply_post_text_dictionary_capacity()` caps per-source counts via
     `WORKFLOW_DICTIONARY_MAX_SEARCH_RESULTS`, `WORKFLOW_DICTIONARY_MAX_COMMENTS`,
     `WORKFLOW_ANGLES_MAX_INPUT_BLOCKS` (lines 188–220).
2. `src/workflows/pipelines/gen_angles.py`
   - `preview_post()` builds the entry bundle, then in **extractive mode**
     (`WORKFLOW_STEGO_ANGLES_GENERATION_MODE=extractive_zero_kld`, the default we
     compare) calls `_generate_angles_extractive(entries)` (line 250).
   - `_entry_to_angles(entry, source_document)` (lines 82–94) splits each entry into
     sentences via `_sentence_candidates()` (24–220 chars), keeps up to **2** per
     entry, and sets `source_quote == tangent == sentence`, `category` from source.
   - `_dedupe_angles()` (lines 97–110) dedupes only by exact casefold of
     `(source_quote, tangent, category)`.
   - `_apply_angle_relevance_gate()` (lines 113–132) calls the naturalness gate.
   - Result is capped to `get_workflow_angles_max_output()` and stored on the post as
     `angles` + `options_count`.
3. `src/workflows/utils/naturalness_gate.py`
   - `score_angle_relevance()` / `filter_angles_for_post()` (lines 156–224): token
     overlap between angle tokens and post+comment tokens. In default **middle**
     mode a tangent is rejected **only if overlap is exactly zero** (line 181). One
     or two shared 4+ char words is enough to pass — which is why search-doc
     tangents survive.
4. `src/workflows/utils/stego_codec.py`
   - `flatten_angle_groups()` assigns each angle a canonical `idx` (lines 670–677).
   - `embed_in_angle_selection()` (lines 684–718): `bits_count = get_bit_width(len(angles) - 1)`.
     **Capacity is a pure function of the tangent count** — every angle we keep adds
     addressable bits, which is why the current code maximizes count over quality.

Key invariants we must not break:
- The **receiver rebuilds the identical tangent list** from the same post
  (`ReceiverPipeline.rebuild_context` → same `flatten_nested_angles`). Any change to
  DB construction must be **deterministic** and reproducible from the persisted post
  alone (no wall-clock, no RNG without a stored seed, no model nondeterminism inside
  the DB ordering).
- `idx` ordering defines the bit mapping. Reordering/filtering the DB changes which
  bits map to which tangent — fine for *new* prepared posts, but a prepared post and
  its receiver must use the same DB. (This is why revamped posts persist to a
  separate folder — see [[prepared-posts-separate-persistence]].)

## 3. Design

Introduce a dedicated, deterministic **TangentDB builder** that runs after raw
extraction and before capacity capping, with three ordered stages: **anchor →
score → diversify**. Gate it behind a config flag so the current behavior is the
default until we validate.

### 3.1 New module

`src/workflows/utils/tangent_db.py` — pure functions, Pydantic v2 models, ≤25-line
functions. Business logic stays pure (input → output, deterministic); no I/O.

```
build_tangent_db(candidates: list[AngleCandidate], post: PostContext, cfg: TangentDbConfig)
    -> TangentDbResult
```

`AngleCandidate` = existing angle dict fields (`source_quote`, `tangent`,
`category`, `source_document`). `TangentDbResult` carries the ordered kept list plus
a **`tangent_db_report`** (see §3.5) for observability and audit.

### 3.2 Stage A — Relevance anchor (fixes drift)

Goal: every retained tangent must be about the **thread**, not an arbitrary research
doc. Replace the "any overlap passes" rule with a graded, source-weighted relevance
score:

- **Anchor text** = post title + selftext + top-K comment bodies (the text a human
  reader actually sees in-thread). Reuse `_context_texts_from_post()` from
  `naturalness_gate.py`.
- **Relevance score** per candidate = weighted token overlap with the anchor, using
  the existing `tokenize_content_words()` (4+ chars, stopword-filtered), plus:
  - **Source prior**: `comments` and `post` sources get a higher base weight than
    `search_results`. A search-doc tangent must clear a *higher* overlap bar to be
    admitted (it may still enter, but only if it genuinely echoes the thread topic).
  - **IDF-ish weighting** (optional, deterministic): weight overlap tokens by
    inverse frequency across the post so generic tokens ("people", "government")
    count less than salient ones ("bounty", "flood", "camp").
- **Hard admission threshold** `min_relevance` (config): candidates below it are
  dropped, with reason `low_thread_relevance`. This is stricter than middle mode's
  zero-overlap rule and directly targets drift. Keep `strict`/`middle`/`report`
  parity by mapping the existing modes onto thresholds so we don't fork the gate.

Deterministic tie-breaking: sort by `(relevance desc, source_priority desc,
idx asc)` so ordering is stable and receiver-reproducible.

### 3.3 Stage B — Distinctness / de-duplication (fixes near-dupes)

Goal: the DB should not spend addressable bits on tangents that say the same thing.
Exact-string dedup (today) misses paraphrases and sentence fragments of the same
idea.

- **Lexical near-duplicate filter**: compute a token set (or character n-gram set)
  per candidate; drop a candidate whose **Jaccard similarity** to an already-kept
  tangent exceeds `max_similarity` (config, e.g. 0.7). Greedy: iterate in Stage-A
  relevance order, keep a tangent only if it is far enough from all kept ones.
  Deterministic and cheap; no embeddings required for v1.
- **Optional semantic distinctness (v2)**: if we want paraphrase-level dedup, use
  the existing `services/semantic_service.py` embeddings to cluster candidates and
  keep one representative per cluster. Must be **deterministic** (fixed model +
  rounding) and reproducible by the receiver, or it must run only at *prepare* time
  and the resulting DB persisted verbatim (preferred — receiver reads the stored
  DB, never recomputes embeddings). Flag: `TANGENT_DB_SEMANTIC_DEDUP`.
- Record every drop with reason `near_duplicate_of:<idx>` in the report.

Tension to manage explicitly: **relevance pulls tangents toward the thread topic
(more similar to each other); distinctness pushes them apart (risking drift back
toward off-topic).** Resolve by ordering the stages — relevance is a *hard gate
first*, distinctness only *de-duplicates within the already-relevant set*. Never let
distinctness re-admit an off-topic tangent to hit a count target.

### 3.4 Stage C — Capacity reconciliation

Capacity (bits) still equals `get_bit_width(len(db) - 1)`. Two design choices:

- **Quality-first (recommended):** keep only tangents that pass A and B, then cap to
  `get_workflow_angles_max_output()`. Capacity floats down to the number of good
  tangents. Honest, and it is exactly the "pure-channel capacity" the ZLG page's
  correction note already reports.
- **Capacity-floor (optional):** if a post yields fewer than `min_db_size` distinct
  relevant tangents, either (a) accept the lower capacity, or (b) relax
  `max_similarity` in controlled steps until the floor is met, logging the
  relaxation. Never relax `min_relevance` — drift is the thing we are fixing.

Expose the trade knobs so the metrics work ([[method-comparison-metrics-v2]]) can
sweep them.

### 3.5 Observability: `tangent_db_report`

Persist alongside the angles (and echo into `sender_audit`) so the frontend and the
metrics scripts can inspect DB quality:

```
{
  "builder_version": "tangent_db_v1",
  "input_candidate_count": N,
  "kept_count": K,
  "dropped": {"low_thread_relevance": a, "near_duplicate": b, "capped": c},
  "relevance": {"min": .., "median": .., "threshold": ..},
  "distinctness": {"mean_pairwise_jaccard": .., "max_similarity": ..},
  "source_mix_kept": {"post": .., "comments": .., "search_results": ..},
  "config": { ...effective knobs... },
  "config_hash": "..."          // for receiver-parity assertions
}
```

`builder_version` + `config_hash` let us detect when a persisted post was built with
a different DB recipe than the receiver expects (surface as a warning, mirroring the
existing dictionary-hash drift check).

## 4. Config surface (new env vars)

Follow the `get_workflow_*` + capacity-tier pattern in `src/infrastructure/config.py`
(near lines 527–548). All default to values that **reproduce today's behavior** when
the master flag is off.

| Env var | Meaning | Default |
|---|---|---|
| `WORKFLOW_TANGENT_DB_BUILDER` | `legacy` \| `v1` | `legacy` |
| `WORKFLOW_TANGENT_DB_MIN_RELEVANCE` | Stage-A admission threshold | tuned per profile |
| `WORKFLOW_TANGENT_DB_SEARCH_RELEVANCE_MULT` | extra bar for search-doc tangents | `>1.0` |
| `WORKFLOW_TANGENT_DB_MAX_SIMILARITY` | Stage-B Jaccard cap | `0.7` |
| `WORKFLOW_TANGENT_DB_MIN_SIZE` | capacity floor (0 = disabled) | `0` |
| `WORKFLOW_TANGENT_DB_SEMANTIC_DEDUP` | enable embedding dedup | `off` |

Add to `get_workflow_capacity_settings()` output so runs self-describe.

## 5. Integration points (surgical)

1. `gen_angles.py::_generate_angles_extractive` (and the LLM/analyze paths that build
   `angles`): after producing raw candidates and before `_apply_angle_relevance_gate`
   / capping, call `build_tangent_db(...)` when `WORKFLOW_TANGENT_DB_BUILDER=v1`.
   When `legacy`, keep the current `_dedupe_angles` + gate path untouched.
2. Store `tangent_db_report` on the processed post (new key, e.g.
   `tangent_db_report`) and thread it into `StegoPipeline.encode`'s `sender_audit`
   in `src/workflows/pipelines/stego.py`.
3. `stego_codec.py`: **no change** to `embed_in_angle_selection` /
   `flatten_angle_groups` — they consume whatever `angles` the post carries. The DB
   is already the source of truth; we are only changing *what goes into it*.
4. Receiver (`src/workflows/pipelines/receiver.py`): **no logic change** — it rebuilds
   angles from the persisted post. Add a parity assertion: if the post carries a
   `tangent_db_report.config_hash`, the receiver recomputes it from its effective
   config and warns on mismatch (does not hard-fail; the stored DB still governs).

## 6. Phased rollout

- **Phase 0 — report-only:** implement builder, run it in shadow (compute
  `tangent_db_report` and log kept/dropped) but still emit legacy angles. Compare DB
  quality stats on the existing 47 posts. No behavior change.
- **Phase 1 — relevance anchor on:** enable Stage A only. Re-prepare posts to the
  separate folder ([[prepared-posts-separate-persistence]]); regenerate stego;
  re-run sus-detection. Expect drift-driven detections to fall.
- **Phase 2 — distinctness on:** enable Stage B; verify capacity impact and that
  distinctness didn't reintroduce drift.
- **Phase 3 — tune + optional semantic dedup:** sweep thresholds against the
  metrics-v2 dashboard; decide defaults per capacity profile.

## 7. Testing

New `src/tests/test_tangent_db.py`:
- Determinism: same input → identical DB and report (byte-stable ordering).
- Relevance: off-topic search-doc sentence with a single shared token is dropped
  under v1 but passed under legacy (guards the exact drift case).
- Distinctness: two near-paraphrase sentences → one kept, drop reason recorded.
- Capacity floor: `MIN_SIZE` relaxes similarity, never relevance; logs relaxation.
- Parity: `config_hash` stable across processes for identical config.

Regression gate (per CLAUDE.md): after changes run `test_stego_codec.py`,
`test_pipeline_gen_angles.py`, `test_receiver_pipeline.py`, `test_naturalness_gate.py`,
`test_pipeline_stego.py`, then full `uv run pytest -q` + `uv run pyright`.

## 8. Risks / decisions to make

- **Capacity drop is expected and acceptable** — it is the honest pure-channel
  number. Confirm the paper framing (capacity vs naturalness trade) is fine with a
  lower, cleaner capacity.
- **Prompt red line:** this plan touches *angle selection*, not workflow/system LLM
  prompts. Do **not** edit `config/workflow_llm_prompts.json` or
  `workflow_llm_prompts.py` here. If a prompt tweak turns out desirable, it is a
  separate, double-confirmed, standalone commit.
- **Determinism of semantic dedup** is the main hazard; default it off and, if used,
  freeze it at prepare time so the receiver only ever reads the persisted DB.
- **Receiver parity:** never let the receiver *recompute* the DB with different
  config; the persisted post is authoritative.
