# LUCID continuous generation campaign

**Last operational snapshot:** 2026-08-14 15:31 Asia/Damascus  
**Dataset:** `datasets/prep_runs/LUCID/tangents_db_v1_fresh`  
**Viewer:** `http://localhost:3000/lucid` when `stego-results-viewer` is running

This document records the clean LUCID dataset campaign, search/fetch quota
workarounds, continuous local-GPU workers, watchdog and Windows startup setup,
validation results, and results-viewer integration completed on 2026-08-14.

## Purpose and scope

The campaign grows an isolated, clean preparation corpus for Project LUCID and
TangentsDB-v1 research validation. It runs these stages independently:

1. `data_load`: fetch article content for unused cleaned Reddit/news posts.
2. `research`: generate search terms, find relevant sources, and fetch their text.
3. `angles`: generate and retain TangentsDB-v1 angle candidates.

The campaign does **not** currently run stego encoding or evaluation. At the
snapshot above, `output-results` contained zero JSON files. Use the prepared
`news_angles` corpus as the input to a later controlled evaluation; do not treat
prep counts as stego success results.

The isolated root prevents this work from modifying the historical global
`datasets/news_researched` cache.

## Results timeline

| Point | Fetched | Researched files | Angle files | Notes |
| --- | ---: | ---: | ---: | --- |
| Initial clean baseline | 184 | 33 | 33 | Before the four-hour campaign |
| Four-hour validated handoff | 1,210 | 188 | 54 | Eight new empty research artifacts quarantined |
| Persistent snapshot, 15:31 | 1,676 | 201 | 75 | All forever workers and watchdog alive |

The first monitored campaign ran for 4 hours 8 minutes. At handoff, the corpus
contained 161 research artifacts with non-empty sources, 27 inherited empty
research artifacts, 48 full 32-angle artifacts, and six shorter historical
angle artifacts. These values are a point-in-time validation; the live viewer
recomputes current counts and integrity summaries on reload.

Recoverable quarantine directories created during cleanup:

- `service/quarantine_bing_web_rss_20260814-103547`: low-relevance Bing Web RSS
  research artifacts from the early fallback experiment.
- `service/quarantine_empty_research_20260814-134351`: eight campaign-era empty
  research artifacts produced when term generation was blocked.

Nothing in these directories was deleted.

## Quota and fetch workarounds

### Search fallback chain

`src/services/search_service.py` and
`src/workflows/pipelines/research.py` now use this order:

1. Google Custom Search while its quota circuit is closed.
2. DuckDuckGo.
3. Yahoo News search, resolving Yahoo redirect URLs to publisher URLs.
4. Google News RSS.
5. Bing News RSS.
6. Metered ScrapingDog Bing as the last fallback.

Fallback hits pass a lexical relevance check. A quota error or empty response
for one term skips that term rather than aborting the whole post. If no relevant
results or fetched pages survive, the post fails without writing a new empty
research artifact.

Do not re-enable Bing Web RSS in the active chain without a new quality audit;
its results were too weak for this corpus.

### Term-generation failure

When model-generated search terms are unavailable, for example because a Gemini
key returns `API_KEY_SERVICE_BLOCKED`, research uses the post title as a
deterministic fallback query. This avoids a slow failed model call becoming an
empty `news_researched` file.

### URL content fetch

`WORKFLOW_URL_FETCH_JINA_FIRST=1` routes publisher pages through Jina Reader
before the browser extraction path. This avoids the missing Playwright browser
bottleneck and reduces dependence on model-based extraction. Jina 429/503
responses use short retries.

### Model retry behavior

The LLM adapter reads provider retry hints such as `Retry-After`, Gemini
`retry in Xs`, and `retryDelay`. Angle generation therefore waits for the stated
limit instead of repeatedly failing immediately.

## Continuous processing architecture

### Worker command

`scripts/run_lucid_generation_campaign.py` supports `--forever`. In this mode
`deadline_utc` is empty and the worker continues until explicitly stopped.

The active lanes use these effective commands:

```powershell
uv run python scripts/run_lucid_generation_campaign.py `
  --dataset-root datasets/prep_runs/LUCID/tangents_db_v1_fresh `
  --forever `
  --stage-mode angles `
  --llm-backend lm_studio `
  --batch-count 1 `
  --failure-cooldown-seconds 5
```

The research lane uses the same options with `--stage-mode research`. The
data-load lane uses `--stage-mode data_load --batch-count 5`.

The model-heavy research and angle lanes share LM Studio at
`http://127.0.0.1:8081`. The configured default model is
`qwen/qwen3.5-9b`, which was confirmed loaded before launch.

### Watchdog

Start or adopt all persistent lanes with:

```powershell
.\scripts\start_lucid_forever_supervisor.ps1
```

The supervisor:

- polls every 30 seconds;
- adopts already-live stage workers instead of duplicating them;
- restarts any exited stage with `--forever --llm-backend lm_studio`;
- writes `service/campaign_forever_supervisor_state.json`;
- protects against duplicate supervisors with
  `service/campaign_forever_supervisor.pid`.

A controlled test force-stopped the data-load child. The supervisor replaced it
with a new live PID within 38 seconds, confirming restart behavior.

### Windows login persistence

Registering a Scheduled Task was attempted but Windows denied it without
administrator rights. The non-elevated replacement is this per-user Startup
entry:

```text
C:\Users\ASUS\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Stego-LUCID-Forever.vbs
```

It launches the watchdog hidden after the ASUS user logs in. The supervisor PID
lock makes repeated launches safe. This provides login persistence, not a
pre-login Windows service; no processing occurs while the machine is powered
off or before a user login.

## GPU verification

Immediately after persistent launch, the angle worker drove the RTX 5060 Ti at
approximately:

| Metric | Observed |
| --- | ---: |
| GPU utilization | 88–89% |
| VRAM | about 10.2 / 16.3 GB |
| Power | about 167–170 W |
| Temperature | about 73–77 °C |

Angle artifacts increased from 60 to 75 while the persistent configuration was
being verified. GPU load will naturally fall when the angle queue is empty,
LM Studio is stopped, or the worker is waiting for upstream research.

Check current telemetry with:

```powershell
nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw `
  --format=csv,noheader,nounits
```

## Status and monitoring

### Supervisor and workers

```powershell
$root = "datasets/prep_runs/LUCID/tangents_db_v1_fresh"
Get-Content "$root/service/campaign_forever_supervisor_state.json" -Raw
Get-Content "$root/service/campaign_data_load_state.json" -Raw
Get-Content "$root/service/campaign_research_state.json" -Raw
Get-Content "$root/service/campaign_angles_state.json" -Raw
```

Important state fields:

- `status`: expected to be `running`.
- `pid`: current worker process ID.
- `deadline_utc`: empty for persistent workers.
- `attempts`: attempts since that worker process was launched.
- `last_batch`: most recently completed batch outcome.
- `totals`: filesystem counts at the last heartbeat.

State totals may briefly lag directory counts while a batch is in progress.

### Direct artifact counts

```powershell
$root = "datasets/prep_runs/LUCID/tangents_db_v1_fresh"
(Get-ChildItem "$root/news_url_fetched" -File -Filter *.json).Count
(Get-ChildItem "$root/news_researched" -File -Filter *.json).Count
(Get-ChildItem "$root/news_angles" -File -Filter *.json).Count
(Get-ChildItem "$root/output-results" -File -Filter *.json).Count
```

### Logs

Logs are under `service/` and include timestamped files named like:

```text
YYYYMMDD-HHMMSS.campaign_data_load_forever.stderr.log
YYYYMMDD-HHMMSS.campaign_research_forever.stderr.log
YYYYMMDD-HHMMSS.campaign_angles_forever.stderr.log
campaign_forever_supervisor.stderr.log
```

Treat logs as local operational data. Provider request URLs may include secret
query parameters; do not publish logs or paste unredacted request URLs into
reports.

## Results viewer

`stego-results-viewer` exposes this campaign at `/lucid`. The navigation entry
is **LUCID results** under **Explore results**.

The page reads the isolated dataset and worker state directly and shows:

- live fetched, researched, angle, and stego-output counts;
- research-source and 32-angle integrity checks;
- angle-count distribution, including incomplete artifacts in the denominator;
- worker status, attempts, and deadlines;
- the 12 most recently updated angle-ready samples;
- expandable source and tangent previews;
- the separate historical 500-attempt LUCID evaluation.

Relevant viewer code:

```text
stego-results-viewer/src/app/lucid/page.tsx
stego-results-viewer/src/app/lucid/_lib/fresh-dataset.ts
stego-results-viewer/src/app/lucid/_components/fresh-dataset-section.tsx
```

The loader reads small artifact tails for corpus-wide validation and fully
parses only the 12 displayed samples. This reduced local `/lucid` reloads from
several seconds to roughly one second after compilation.

## Validation performed

Focused backend validation after the quota/fetch changes:

```text
36 tests passed
Ruff: passed
Pyright: 0 errors
```

The persistent campaign script also passed Ruff and Pyright after adding
`--forever`. Both supervisor PowerShell scripts passed parser validation.

Viewer validation:

- Biome passed for changed LUCID files.
- An isolated strict TypeScript check passed for the changed route files.
- `/lucid` returned HTTP 200 and rendered 12 expandable samples without a
  Next.js runtime error.
- The repository-wide viewer typecheck still has unrelated pre-existing errors
  in stego-process and admin files.

## Stop and disable

To stop the watchdog and all three workers, mark their state stopped, and remove
the Windows Startup entry:

```powershell
.\scripts\stop_lucid_forever_supervisor.ps1
```

To stop only the current processes but retain automatic launch at the next
login:

```powershell
.\scripts\stop_lucid_forever_supervisor.ps1 -KeepStartupEntry
```

After a full stop, restart manually with:

```powershell
.\scripts\start_lucid_forever_supervisor.ps1
```

The stop operation does not delete dataset artifacts or quarantine contents.

## Known limitations

- This is a continuous prep campaign, not a completed research comparison.
- Search quality remains source-dependent; failed relevance checks are expected
  and should not be converted into empty researched artifacts.
- The research lane can be slower than data-load and angles due to free-search
  availability and publisher/Jina throttling.
- Six historical short-angle files and 27 inherited empty-research files were
  still present at the four-hour validation point. They remain visible in the
  viewer denominator and must not be silently counted as full valid samples.
- Login persistence requires the ASUS user to log in; it is not a privileged
  Windows service.
- Continuous GPU operation increases heat and power use. LM Studio, NVIDIA
  driver, and system thermal protections remain the authority for safe limits.

## Related files

- `scripts/run_lucid_generation_campaign.py`
- `scripts/start_lucid_forever_supervisor.ps1`
- `scripts/stop_lucid_forever_supervisor.ps1`
- `docs/operations/lucid-fresh-research-service.md`
- `docs/reports/2026-08-08-current-research-state.md`
- `docs/plans/project-lucid-tangentdb-and-feedback-loop.md`
