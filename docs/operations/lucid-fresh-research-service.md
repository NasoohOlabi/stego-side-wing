# LUCID fresh research service (research → angles)

Durable background worker that grows a **new** TangentsDB-v1 prepared corpus.
It does **not** encode stego text. It stops each day-cycle when search quota is
exhausted (or the `news_cleaned` seed has no remaining posts), sleeps 24 hours,
then continues.

Authoritative status context:
[`../reports/2026-08-08-current-research-state.md`](../reports/2026-08-08-current-research-state.md).
This service exists because the historical `datasets/news_researched` cache is
contaminated for some posts; LUCID evaluation needs a fresh researched set.

## What it does

Each cycle:

1. `data-load` — pull unused posts from global `datasets/news_cleaned`
2. `research` — live web search + URL fetch into the isolated root
3. `gen-angles` — build angles with `WORKFLOW_TANGENT_DB_BUILDER=lucid`
4. Stop the cycle when search quota is detected or no posts remain
5. Sleep **24 hours** (configurable), then start another cycle

Search order **per query**:

1. Google Custom Search (CSE keys in `.env`)
2. On Google quota/rate-limit → DuckDuckGo
3. If DDG returns nothing usable → Bing via ScrapingDog (`SCRAPINGDOG_API_KEY`)
4. If all fail after Google quota → cycle ends (`quota_detected`)

Artifacts are written only under the isolated dataset root (default below). The
global `datasets/news_researched` / old LUCID angle lanes are **not** modified.

## Default paths

| Path | Role |
| --- | --- |
| `datasets/prep_runs/LUCID/tangents_db_v1_fresh/` | Isolated `WORKFLOW_DATASET_ROOT` |
| `…/news_url_fetched/` | URL-resolved posts |
| `…/news_researched/` | Fresh researched posts |
| `…/news_angles/` | LUCID TangentsDB-v1 angle artifacts |
| `…/prep_run.json` | Prep-run manifest |
| `…/service/state.json` | Heartbeat / cycle state |
| `…/service/worker.pid` | Service PID |
| `…/service/stop.requested` | Graceful stop sentinel |
| `…/service/service.stdout.log` | Stdout when started via the helper script |
| `…/service/service.stderr.log` | Stderr when started via the helper script |

Environment forced by the service:

- `WORKFLOW_DATASET_ROOT=<dataset-root>`
- `WORKFLOW_TANGENT_DB_BUILDER=lucid`
- `WORKFLOW_CONTEXT_SAMPLER=context_weighted_v2` (if unset)
- `WORKFLOW_DATASET_SEED_GLOBAL=1` (seed corpus stays global `news_cleaned`)

## Requirements

From `stego-side-wing` with `.env` configured:

- Google CSE: `GOOGLE_CSE_ID` + `GOOGLE_API_KEY_1`… (rotated on failure)
- Optional Bing fallback: `SCRAPINGDOG_API_KEY`
- DuckDuckGo Instant Answer API needs no key (weaker organic coverage)
- Workflow LLM for angle generation (`WORKFLOW_LLM_BACKEND`, LM Studio or AI Studio)

## Start (Windows)

From `stego-side-wing`:

```powershell
.\scripts\start_lucid_fresh_research_service.ps1
```

### Persistent local-GPU campaign

For continuous fetch, research, and angle lanes with LM Studio, use:

```powershell
.\scripts\start_lucid_forever_supervisor.ps1
```

The supervisor adopts live lane workers, restarts a lane within 30 seconds if it
exits, and launches replacements with `--forever --llm-backend lm_studio`.
Research and angle generation share the local LM Studio queue; data-load remains
an HTTP/file workload. Worker state is written to
`service/campaign_{data_load,research,angles}_state.json`, and supervisor state
to `service/campaign_forever_supervisor_state.json`.

On the ASUS desktop, the per-user Startup entry
`Stego-LUCID-Forever.vbs` relaunches this supervisor after Windows login. Stop
the persistent workers and remove that Startup entry with:

```powershell
.\scripts\stop_lucid_forever_supervisor.ps1
```

Pass `-KeepStartupEntry` only when stopping the current processes temporarily
but retaining automatic startup for the next login.

Or manually:

```powershell
$env:WORKFLOW_TANGENT_DB_BUILDER = "lucid"
uv run python scripts/run_lucid_fresh_research_service.py `
  --dataset-root datasets/prep_runs/LUCID/tangents_db_v1_fresh `
  --batch-count 1 `
  --batch-size 5 `
  --sleep-hours 24 `
  --log-level INFO
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--once` | One prep cycle, then exit (no sleep) |
| `--max-cycles N` | Stop after N cycles |
| `--sleep-hours 24` | Quota cooldown (default 24) |
| `--no-search-fallbacks` | Google only; stop cycle on first Google quota |
| `--batch-count` / `--batch-size` | Prep batch sizing |

## Stop

Preferred:

```powershell
.\scripts\stop_lucid_fresh_research_service.ps1
```

This creates `service/stop.requested`. The worker notices within ~30s (during
sleep) or at the next batch boundary (during prep), writes `status=stopped`,
and exits. The stop file is removed on clean exit.

Hard stop (last resort):

```powershell
$pid = Get-Content datasets/prep_runs/LUCID/tangents_db_v1_fresh/service/worker.pid
Stop-Process -Id $pid -Force
```

## Status

```powershell
Get-Content datasets/prep_runs/LUCID/tangents_db_v1_fresh/service/state.json
(Get-ChildItem datasets/prep_runs/LUCID/tangents_db_v1_fresh/news_angles -Filter *.json).Count
```

`state.json` fields: `status` (`running_prep` / `sleeping` / `stopped`), `cycle`,
`totals` (fetched/researched/angles counts), `last_stop_reason`,
`last_quota_detected`, `sleeping_until_utc`.

## Process model

- Long-lived Python process (not a Chrome extension / browser job).
- Search uses HTTP APIs (Google CSE, DuckDuckGo, optional ScrapingDog Bing).
- URL content fetch uses the existing research fetch pipeline.
- Safe to leave running overnight; after quota it idles 24h then resumes.
- Re-running start while a live PID exists is refused by the start script.

## After you have enough clean angles

Do **not** mix this root with contaminated `datasets/news_researched`. Point
e2e / benchmarks at:

```text
datasets/prep_runs/LUCID/tangents_db_v1_fresh/news_angles
```

with `WORKFLOW_TANGENT_DB_BUILDER=lucid`, per the LUCID plan freeze/conclusion.

## Related code

- Worker: `scripts/run_lucid_fresh_research_service.py`
- Prep API: `WorkflowRunner.run_prep_until_search_quota`
- Search fallbacks: `ResearchPipeline._web_search_google_or_bing`
- Older one-shot (includes stego): `scripts/run_prep_until_google_quota_then_stego.py`
