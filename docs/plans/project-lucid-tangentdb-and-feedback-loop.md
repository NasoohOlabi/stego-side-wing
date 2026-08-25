# Project LUCID: Tangent Distinctness and Honest Decode Recovery

**LUCID** = **L**inguistic **U**niqueness, **C**ontextual **I**ntegrity, and
**D**ecodable selection.

## Decision

Project LUCID replaces forced recoverability with two upstream safeguards:

1. build a TangentsDB whose entries are distinct, thread-grounded, and
   decodable from natural replies; and
2. use an LLM-assisted feedback loop to improve a failing natural candidate
   without ever injecting a synthetic carrier.

If those safeguards cannot yield an exact decode within a bounded budget, the
encode attempt fails honestly.

## Non-negotiable invariants

- The visible carrier is an LLM-produced reply, never a deterministic template.
- Sender and receiver use one versioned TangentsDB and one codec contract.
- Every accepted output has recorded candidate provenance, prompt hashes,
  model identity, decode outcome, and quality-gate outcome.
- Exact recovery is necessary but not sufficient: thread fit and naturalness
  remain hard acceptance conditions.
- Exhaustion produces a failed attempt with diagnostics, never a fabricated
  success.

## Status (2026-08-08) — refactor complete; code frozen pending clean samples

| Item | State | Notes |
| --- | --- | --- |
| A1–A2 schemas + deterministic scoring | ✅ | `lucid_tangent_db.py` |
| A3 LLM structured critic | ✅ | `lucid_critic.py` + re-score gate |
| A4 freeze/version artifact | ✅ | content hash + post/report persistence |
| Wire `lucid` builder into `gen_angles` | ✅ | `WORKFLOW_TANGENT_DB_BUILDER=lucid` (default still `legacy`) |
| Sender/receiver codebook hash parity | ✅ | `_lucid_tangents_db_parity_mismatch` |
| B1 failure taxonomy persistence | ✅ | e2e `encode_failure` projection |
| B2 revision prompts | ✅ | `lucid_revision` in workflow prompts JSON |
| B3 revalidate revisions + provenance | ✅ | sharpen path + attempt/feedback/prompt_hash |
| B4 honest stop + typed failure | ✅ | encode failure dict + e2e taxonomy fields |
| Dashboard / benchmark accounting | ✅ | `/lucid` failure codes + taxonomy + baseline banner |
| Git provenance on workload runs | ✅ | commit/branch/dirty/command/manifest hash |
| Frozen-manifest pilot scaffold | ✅ | `scripts/prepare_lucid_tangents_db_pilot.py` + `datasets/prep_runs/LUCID/tangents_db_v1/pilot.json` |

**Code freeze:** the LUCID refactor implementation is considered complete. Do **not**
change TangentsDB admission, promising-candidate / sharpen selection, encode or
decode prompts, or failure taxonomy based on the first TangentsDB-v1 bulk run
until a clean researched corpus yields enough uncontaminated samples. Ship only
docs, accounting, or unrelated work unless a change is independently justified
outside that contaminated evidence.

### Empirical evidence (do not over-claim)

**Sampler baseline (not TangentsDB-v1):**
`metrics/e2e_runs/LUCID_context_weighted_v2_balanced_500`
(**381 / 500**, 76.2% ITT; 64 reused posts; dirty worktree based on `0353848`):

- Failure codes: **108** `receiver_angle_mismatch`, **11** `generation_failure`.
- Candidate rejects dominated by far `decode_mismatch`.
- Angle artifacts were **context-weighted v2**, **not** TangentsDB-v1.

**First TangentsDB-v1 bulk attempt (contaminated research cache — not decision-grade):**
`metrics/e2e_runs/LUCID_tangents_db_v1_balanced_500`
(**371 / 500**, 74.2% ITT; 197 unique angle posts with reuse; builder `lucid`):

- Failures: **121** `receiver_angle_mismatch`, **7** `generation_failure`,
  **1** `stego_invalid_json`.
- Angles were regenerated with `WORKFLOW_TANGENT_DB_BUILDER=lucid` under
  `datasets/prep_runs/LUCID/tangents_db_v1/lucid/news_angles`, but
  `search_results` were copied unchanged from `datasets/news_researched`
  (cached mid-2025 / written early 2026). That cache is research-contaminated
  for some posts (off-thread articles mixed into the dictionary).

Treat that TangentsDB-v1 run as a **pipeline smoke / accounting check**, not as
evidence that TangentsDB admission or the revision loop must change.

## Conclusion (2026-08-08) — end of LUCID refactor phase

### What the refactor delivered

Project LUCID’s implementation goal is done: a versioned TangentsDB builder,
honest encode exhaustion with typed failure taxonomy, optional LLM critic and
revision prompts, sender/receiver hash-parity checks, dashboard/accounting
separation of sampler baseline vs TangentsDB-v1, and a frozen-pilot scaffold.
Default builder remains `legacy`; TangentsDB-v1 is opt-in via
`WORKFLOW_TANGENT_DB_BUILDER=lucid`.

### What we learned from the first TangentsDB-v1 500-run (and what we must not conclude)

A traced mismatch on post `1lqptry` (hot-car death thread, selected angle about
**organ procurement**) showed:

1. The decoder and context gate behaved coherently: generated replies stayed on
   the hot-car thread and decoded to other on-thread angles.
2. The selected intent’s `relation` came from **old cached `search_results`**,
   not from a fresh research pass. The TangentsDB angle regen reused
   `datasets/news_researched` byte-identically (including dozens of Kentucky
   organ-harvest articles on a Texas hot-car post).
3. A thin shared phrase (“incident … under investigation by state and federal
   officials”) let an off-thread research topic into the codebook.
4. The same post/angle sometimes succeeded only when a draft lexically echoed
   that thin cue — stochastic, not proof of stable recoverability.
5. Far decode mismatches leave `promising_candidates` empty, so revision may
   not run — noted as a hypothesis only; **not** a fix authorization while
   inputs are contaminated.

**Decision rule:** do not authorize TangentsDB, sharpen, or prompt changes from
contaminated-cache failures. Step back; keep the current code; gather enough
samples on a clean researched set first.

### Freeze and next gate

| Allowed now | Not allowed yet |
| --- | --- |
| Cite refactor as implementation-complete | “Fix mismatch rate” patches motivated by the contaminated 500-run |
| Keep collecting / cleaning research + angles | Prompt edits without the usual double confirmation |
| Report ITT with contamination caveats | Claiming TangentsDB-v1 quality vs sampler baseline as settled |
| ZLG pairing only on frozen clean manifests | Treating `news_researched` organ-polluted posts as TangentsDB ground truth |

**Next gate before any LUCID code change:** a researched corpus (or explicit
filter) with thread-faithful `search_results`, TangentsDB-v1 angles rebuilt from
that corpus, and enough encode/decode samples for failure inspection that are
not dominated by known cache contamination. Until then, the LUCID refactor
phase is closed and the code stays as cemented above.

Authoritative status note:
[`docs/reports/2026-08-08-current-research-state.md`](../reports/2026-08-08-current-research-state.md).

## Architecture note — three angle layers

1. **Context-weighted sampler** (`context_weighted_v2`) — candidate pool source.
2. **`tangent_db` v1** — legacy dict distinctness (`WORKFLOW_TANGENT_DB_BUILDER=v1`).
3. **Project LUCID TangentsDB** — `{subject, relation, thread_cue}` intents
   (`WORKFLOW_TANGENT_DB_BUILDER=lucid`, namespace `project_lucid/tangents_db/v1`).

## How to run the TangentsDB-v1 pilot

```powershell
uv run python scripts/prepare_lucid_tangents_db_pilot.py `
  --root datasets/prep_runs/LUCID/tangents_db_v1 `
  --pilot-id lucid_tangents_db_v1_pilot `
  --materialize-from datasets/news_researched `
  --limit 25

$env:WORKFLOW_TANGENT_DB_BUILDER = "lucid"
uv run python scripts/run_actual_workload_e2e.py `
  --variant balanced `
  --samples-per-profile 25 `
  --angles-dir datasets/prep_runs/LUCID/tangents_db_v1/lucid/news_angles `
  --dataset-dir datasets/news_cleaned `
  --run-dir metrics/e2e_runs/LUCID_tangents_db_v1_pilot_25 `
  --max-retries 1 `
  --log-level INFO
```

Gate any broader benchmark on recovery + naturalness thresholds after manual
failure inspection. Do not pair with unpaired ZLG lanes.

## Acceptance criteria

- No code path can emit a visible carrier that did not come from an LLM call.
- TangentsDB builds report pairwise separation and reject ambiguous entries.
- Failed decode attempts remain visible in artifacts and metrics.
- Every accepted carrier has exact receiver recovery and passes the existing
  contextuality/naturalness gates.
- A reviewer can reproduce an output from its frozen TangentsDB and recorded
  LLM provenance without relying on process memory.
- Dashboard and summaries distinguish “LUCID-named sampler baseline” from
  “TangentsDB-v1 codebook evaluation.”

## Risks to manage

- Greater semantic separation can reduce usable angles and thus capacity.
- LLM critics can introduce generic language; retain deterministic filters.
- Feedback loops can overfit to the decoder; keep human-facing quality gates.
- Naming confusion: `LUCID_context_weighted_v2_*` is not TangentsDB-v1.
