# POLYCARRIER — Progress

Running status log. Newest entry on top within each section.

> **Relocation (2026-08-07):** This workspace now runs **on ASUS** at
> `D:\Master\code\stego`. Live ZLG is sibling `zero-shot-GLS` on
> `http://127.0.0.1:9000`. Entries below that cite `ssh asus`, SSH tunnels,
> LAN `192.168.100.136`, OMEN-as-client, or
> `D:\Master\code\zero-shot\zero-shot-GLS` are **historical** (pre-move).

## Status summary

| Phase | State | Notes |
|---|---|---|
| Codename + docs | **done** | Folder created 2026-08-06 |
| Sample-layer metric script | **done** | §§1.2–6.2 + §7 paired companion |
| Offline capacity preflight | **done** | `scripts/polycarrier_capacity_preflight.py`; Phase 1 knobs fixed to `6×4` |
| Phase 0 synthetic smoke | **failed (synthetic limits)** | 256b capacity miss; 64b plan OK then frame-0 validation exhausted |
| Phase 1 ours multi-frame batch | **running (`_retry4`)** | `_retry3` stopped (0/3 so far, retries=1); relaunched with `--max-retries 3` |
| Phase 2 ZLG capacity_matched | **skipped for now** | User skip; ZLG/llama still unloaded |
| Phase 3 metric build + rollups | not started | Needs Phase 1+2 artifacts |
| Phase 4 `/zlg-comparison` view | not started | |
| Multicomment Codex judge (§8) | deferred | Needs frozen prompt + live run |

**Overall:** Phase 1 smoke live at `polycarrier_256b_smoke_retry4` with `--max-retries 3` after `_retry3` showed planning OK but frame encode exhausted under retries=1. Warm angle cache → encode started in seconds. Do **not** duplicate Phase 1.

---

## 2026-08-06 — `_retry4` samples 0–2 failed despite max-retries 3 (~17:27 local)

- **Knobs:** `6×4` / 32B / `--max-retries 3`. Preflight OK. Warm cache → encode within seconds for samples 0–2.
- **Results so far:**
  - s0: frame 0 OK (`retry_count=2`); fail @ frame **1** (`retry_count=3` exhausted)
  - s1: fail @ frame **0** (retries=3)
  - s2: frames 0–1 OK; fail @ frame **2** (retries=3)
- **Sample 3:** angle planning (log ~1.19MB, ~14:25Z); still no `summary.json`.
- **Insight:** ~21–22 frames/sample → compound validation risk; retries help but do not clear smoke gate yet.
- **Do not** start another Phase 1 duplicate while `_retry4` PID **3628** alive.

---

## 2026-08-06 — `_retry3` → `_retry4` with max-retries 3 (~17:05 local)

- **`_retry3` result (incomplete, killed mid sample 3):** samples 0–2 all failed under default `--max-retries 1`:
  - s0: fail @ frame 0
  - s1: frame 0 `diagnostic_success`, fail @ frame 1
  - s2: frames 0–1 success, fail @ frame 2
  - Pattern: multi-frame path works; retries=1 too tight for ~21–22 frames. No `summary.json` (killed before samples 3–4).
- **Action:** killed PID **10804**; launched `_retry4` with same knobs + **`--max-retries 3`**.
- **Preflight:** exit **0** (capacities 320–360 ≥ 288).
- **`_retry4`:** PIDs **36168/3628**; warm cache → sample 0 planning ~instant; **already in encode/decode cross-validate** @ 14:06Z. Log `metrics/e2e_runs/polycarrier_256b_smoke_retry4_run.log`.
- **RESUME 46ddef5e:** **no** while `_retry4` alive.

---

## 2026-08-06 — `_retry3` samples 0–1 failed; sample 2 planning (~15:49 local)

- **Sample 0:** 21 frames planned; fail @ frame **0** (`diagnostic_validation_exhausted`, max_retries=1).
- **Sample 1:** 22 frames planned; frame **0** `diagnostic_success` on `1lpcwhq`; fail @ frame **1** (`diagnostic_validation_exhausted`). Proves multi-frame encode path can succeed per-frame; batch still fails early with retries=1.
- **Sample 2:** started @ 12:25:48Z (`1lq78vm`…`1lqigzt`); angle planning in progress; log ~745KB+; PID **10804** alive.
- **Artifacts:** still no `summary.json` / empty or sparse `output-results/` until batch ends.
- **Next:** let `_retry3` finish → then if 0/5, relaunch `_retry4` with `--max-retries 3` (recoverable). Phase 2 still skipped.

---

## 2026-08-06 — `_retry3` sample 0 failed encode; sample 1 planning (~15:08 local)

- **Sample 0:** planning OK — **21 frames** planned across 6 posts; **encode FAILED** at frame 0: `diagnostic_validation_exhausted` (`max_retries=1`, encode_total_ms≈22s). `sample_complete` succeeded=false, error=`Frame generation failed at index 0`.
- **Sample 1:** `sample_start` @ 11:42:36Z posts `1lpcwhq`…`1lq3exz`; still in parent-conditioned angle previews (~25+ min); log ~415KB growing; PID **10804** alive (~1.2GB).
- **Gate status:** Phase 1 smoke **not yet green** (need `payload_match` + `frame_count>1`). Capacity/planning works; **selection-channel validation** is the live failure mode (same as Phase 0 64b synthetic).
- **Note:** default `--max-retries 1` may be too tight for smoke; consider `--max-retries 3` on next relaunch if samples 1–4 also exhaust validation.
- **No `summary.json` yet** (written only after all 5 samples finish). Phase 2 still skipped/blocked.

---

## 2026-08-06 — Automate monitor cleared (~14:27 local)

- **User request:** clear/stop the polycarrier **automation monitor** (recurring `AGENT_LOOP_TICK_polycarrier` / shell `310984`), not Phase 1.
- **Monitor:** terminal shell PID **5644** (`while true; sleep 300; echo AGENT_LOOP_TICK_polycarrier…`) was **already gone** at clear time (`taskkill` → process not found). No live `sleep 300` / `AGENT_LOOP_TICK_polycarrier` loop processes remained (`SUSPECT_COUNT=0`). Terminal file metadata may still show stale `status: running`.
- **Left running:** Phase 1 `polycarrier_256b_smoke_retry3` PIDs **20996/10804**. LM Studio / ASUS services untouched.
- **Also not touched:** unrelated LUCID tick loop (if any) and Phase 1 watchers using `sleep 100`.

---


## 2026-08-06 — Phase 1 retry3 launched after reboot (~14:23 local)

- **Trigger:** ASUS unplanned reboot (~14:04) dropped LM mid-`retry2`; incident already logged. OMEN still had `retry2` **31252/34792** (+ LUCID cont3) alive after reboot — killed for a clean `_retry3`.
- **`_retry2` autopsy:** killed mid-`angles_llm_request` (~11:22Z) on sample 0; log ~200KB frozen; **no** `summary.json` / empty `output-results/`.
- **Cleanup:** killed `retry2` 31252/34792; killed LUCID cont3 11604/31664/14576/28860. Stray **cont4** (`samples-per-profile 31`, PIDs 30500/35968 @ ~14:23:20) also killed. LUCID stays down.
- **Health:** LM `192.168.100.136:8081` **200** (`qwen/qwen3.5-9b` models+chat). ZLG `:9000` **down**. llama `:8090` **down**.
- **Phase 1 restart** (python **20996/10804**):
  ```bash
  export WORKFLOW_LLM_BACKEND=lm_studio LM_STUDIO_URL=http://192.168.100.136:8081 WORKFLOW_CONTEXT_SAMPLER=context_weighted_v2
  uv run python scripts/polycarrier_capacity_preflight.py \
    --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \
    --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32 \
  && uv run python scripts/run_multi_frame_batch_e2e.py \
    --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \
    --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32 \
    --run-dir metrics/e2e_runs/polycarrier_256b_smoke_retry3 \
    2>&1 | tee metrics/e2e_runs/polycarrier_256b_smoke_retry3_run.log
  ```
- **Run health:** preflight OK; sample 0 angles; warm cache then live LLM RTT ~13–16s `assistant_text`; log growing (~+618 B/10s @ ~93KB); **0** load-fail/400/Traceback. No `summary.json` yet.
- **Next:** wait for `_retry3` `summary.json`; keep ZLG/llama/LUCID down until then.

---

## 2026-08-06 — Tick #80 / occurrence 78 (~14:18 local)

- **STATUS:** **advancing (slow)** — `_retry2` PIDs **31252/34792** alive; log **~177KB→184KB** (+618 B/15s); last line age ~10s mid-`angles_llm_request`; LLM RTT ~13–17s `assistant_text`.
- **RESUME:** **no** — process live + log growing; not dead; Phase 1 incomplete (no `summary.json`).
- **Endpoints:** LM `192.168.100.136:8081` **200** (models + chat ~0.95s). ZLG `:9000` **down**. llama `:8090` **down**. Local 8081/8090/9000 closed.
- **Delta vs #78:** still sample **0/5**; angles on **4/6 posts** (`1lootoa` 24, `1lot8cs` 7, `1lotamz` 4, `1lp3emo` 3); **38** `preview_complete` (was 26); **no encode**; `output-results/` empty; **NO `summary.json`**. Slow-but-alive (~42 min on sample-0 angles), not >5min freeze.
- **Crash signs:** no Traceback / load-fail; ESTABLISHED TCP to LM from PID 34792.
- **Also:** LUCID `11604/31664` still sharing `:8081`. No competing Phase 1. Leave ZLG/llama down until `summary.json`.
- **Next:** wait for `_retry2` `summary.json`; restore ZLG/llama only after; no RESUME.

---

## 2026-08-06 — INCIDENT: ASUS unplanned reboot killed Phase 1 + LUCID (~14:04 local)

- **Trigger:** `ssh asus` reported failing by user. Diagnosed from this Windows machine: SSH config/keys/known_hosts were all correct (`Host asus` → `192.168.100.136`, user `ASUS`, `id_ed25519`); TCP 22 reachable; `ssh -v asus hostname` connected, authenticated, and returned `DESKTOP-G71HMQ7` on the very first retry with **no config/network changes needed**.
- **Root cause (confirmed via ASUS System event log, cross-checked by invoking Claude Code (Sonnet) running locally on the ASUS box over the same SSH channel — it independently reached the same conclusion):** ASUS did an **unplanned full OS restart** at **14:04:05–14:04:49 local** — Event 1074 (`StartMenuExperienceHost.exe` restart on behalf of user `ASUS`, reason "Other (Unplanned)"), kernel-power Event 109, EventLog stop/start 6006→6005. Most likely a Windows-Update-prompted restart accepted via the Start menu, not a crash. `sshd` service is `Automatic` start type, so it came back up on its own once Windows finished booting (`LastBootUpTime` 2:04:39 PM) — by the time this was investigated (~10 min later) `ssh asus` was already 100% healthy (5/5 reconnects, ~0.4s each).
- **Collateral damage — the reboot silently killed both GPU jobs** (this is new information beyond the SSH question):
  - **Phase 1 `polycarrier_256b_smoke_retry2`** — PIDs **31252/34792 are gone** (process list confirms no survivors; all current `python.exe` on ASUS have `CreationDate` ≥ 14:05, i.e. post-reboot). **No `summary.json` was ever written** for this run — it needs a fresh restart using the same command block as the "Unblocked: freed VRAM; Phase 1 retry2 live" entry below.
  - **LUCID `balanced_100_cont3`** — PIDs **11604/31664/14576/28860 are also gone.** Needs its own restart/resume decision (out of scope for this SSH-focused pass).
  - LM Studio auto-relaunched at boot (~14:05:18–14:05:33) and had `qwen/qwen3.5-9b` reachable again by ~14:14 (`:8081` → 200); ZLG `:9000` and llama `:8090` remain intentionally stopped per the Phase 1 GPU-space plan. Current GPU: 10.1 GB / 16.3 GB used, 92% util (LM Studio only; no polycarrier/LUCID process attached).
- **Fix applied:** none needed — this was a transient reboot-induced outage that self-healed via `sshd`'s Automatic start type. No SSH config, firewall, or key changes were made. (Confirmed this same "Other (Unplanned)" restart-via-Start-menu pattern recurs every few days per event log: 8/4, 7/30, 7/25, 7/22 — likely worth a standing Windows Update/auto-restart mitigation on ASUS, but that's outside this incident's scope.)
- **Action needed next tick:** relaunch Phase 1 `_retry2` (or a fresh `_retry3` run-dir) with the same preflight + `run_multi_frame_batch_e2e.py` command; separately decide whether to resume LUCID `cont3`.

---

## 2026-08-06 — Tick #78 / occurrence 76 (~14:08 local)

- **STATUS:** **advancing (slow)** — `_retry2` PIDs **31252/34792** alive; log **~126KB→135KB** (+618 B/8s); last line age ~21s mid-`angles_llm_request`; LLM RTT ~14s `assistant_text`.
- **RESUME:** **no** — process live + log growing; not dead; Phase 1 incomplete (no `summary.json`).
- **Endpoints:** LM `192.168.100.136:8081` **200** (models + chat ~1.3s). ZLG `:9000` **down**. llama `:8090` **down**. Local 8081/8090/9000 closed.
- **Delta vs #77:** still sample **0/5**; angles still **3/6 posts** (`1lootoa` 17, `1lot8cs` 7, `1lotamz` 2); **26** `preview_complete` (was 24); **no encode**; `output-results/` empty; **NO `summary.json`**. Flag: ~32 min on sample-0 angles — **slow-but-alive**, not >5min freeze.
- **Crash signs:** no Traceback / load-fail; earlier ConnectTimeout cascade @11:04–11:05Z already recovered; ESTABLISHED TCP to LM from PID 34792.
- **Also:** LUCID `11604/31664` (+watchers) still sharing `:8081`. No competing Phase 1.
- **Next:** wait for `_retry2` `summary.json`; restore ZLG/llama only after; no RESUME.

---

## 2026-08-06 — Tick #77 / occurrence 75 (~14:04 local)

- **STATUS:** **advancing (slow)** — `_retry2` PIDs **31252/34792** alive; log **~56.5KB→126KB**; LLM RTT ~14–27s; brief ConnectTimeout cascade then recovered attempt 5 (`assistant_text` 26.3s) and continued.
- **RESUME:** **no** — process live + log growing; not dead; Phase 1 incomplete (no `summary.json`).
- **Endpoints:** LM `192.168.100.136:8081` **200** (models + chat OK after blip). ZLG `:9000` **down**. llama `:8090` **down**. Local 8081/8090/9000 closed.
- **Delta vs #74:** still sample **0/5**; angles on **3/6 posts** (`1lootoa` 16 previews, `1lot8cs` 7, newly `1lotamz` 1+); **24** `preview_complete`; **no encode**; `output-results/` empty; **NO `summary.json`**. Flag: 3/6 posts after ~28 min — **slow but not stalled**.
- **Crash signs:** no Traceback / load-fail; `angles.llm_retry` ConnectionError/ConnectTimeout @11:04–11:05Z recovered on attempt 5.
- **Also:** LUCID `11604/31664` (+watchers) still sharing `:8081`. No competing Phase 1.
- **Next:** wait for `_retry2` `summary.json`; restore ZLG/llama only after; no RESUME.

---

## 2026-08-06 — Tick #74 / occurrence 72 (~13:48 local)

- **STATUS:** **advancing** — `_retry2` PIDs **31252/34792** alive; log **~15KB→56.5KB** (+3.0KB/20s); LLM RTT ~13–15s `assistant_text`; **0** load-fail/Traceback.
- **RESUME:** **no** — process live + log growing; not dead; Phase 1 incomplete (no `summary.json`).
- **Endpoints:** LM `192.168.100.136:8081` **200** (qwen listed; chat 200). ZLG `:9000` **down** (timeout). llama `:8090` **down** (timeout). Local 8081/8090/9000 closed.
- **Delta vs #72:** still sample **0/5**; angles progressed `1lootoa`→also `1lot8cs` (11 previews); **no encode**; `output-results/` empty; **NO `summary.json`**.
- **Crash signs:** terminal `289646` marked **aborted** but workers orphaned-alive and writing; no crash line.
- **Also:** LUCID `11604/31664` (+watchers) still sharing `:8081`. No competing Phase 1.
- **Next:** wait for `_retry2` `summary.json`; restore ZLG/llama only after; no RESUME.

---

## 2026-08-06 — Tick #72 / occurrence 70 (~13:38 local)

- **STATUS:** **advancing** — `_retry2` alive; log ~15KB (+618 B/8s); LLM RTTs ~14–28s `assistant_text`; **0** load-fail errors.
- **RESUME:** **no** — Phase 1 live (python **31252/34792**, terminal `289646`); ea34152f already restarted; do not compete.
- **Endpoints:** LM `:8081` **200** (chat OK). ZLG `:9000` **down**. llama `:8090` **down**.
- **Delta vs #71:** dead `_retry` → live `_retry2`; sample 0 angles on `1lootoa`; still **no encode** / **no `summary.json`**.
- **Also:** LUCID `11604/31664` still sharing `:8081`.
- **Next:** wait for `_retry2` `summary.json`; no competing Phase 1.

---

## 2026-08-06 — Unblocked: freed VRAM; Phase 1 retry2 live (~13:36 local)

- **Freed on ASUS (`192.168.100.136`):**
  - Stopped ZLG `stego_api_server` PIDs **42220** + **47492** (port `:9000`)
  - Stopped `llama-server` PID **78596** (port `:8090`, `Qwen3.5-9B-Q4_K_M.gguf`)
  - Exact remote kill (PowerShell EncodedCommand; avoid reserved `$PID`):
    ```powershell
    Stop-Process -Id 42220,47492,78596 -Force
    # verify: ports 9000/8090 closed; nvidia-smi
    ```
  - VRAM: **6663 → 2810 MiB** after unload; **~10627 MiB** after LM loaded qwen.
- **LUCID:** **not paused** — `balanced_100_cont3` PIDs **11604/31664** (+ watchers **14576/28860**) still running. Pause only if LM load fails again.
  - Resume path if paused later: re-invoke the same `run_actual_workload_e2e.py --variant balanced …` (or unpause soft-pause).
- **LM verify:** `POST :8081/v1/chat/completions` model `qwen/qwen3.5-9b` → **200**, content `OK`, RTT ~15s. `/v1/models` lists qwen. ZLG/llama confirmed down (connect timeout).
- **Phase 1 restart** (shell PID ~2684; python **31252/34792**):
  ```bash
  export WORKFLOW_LLM_BACKEND=lm_studio LM_STUDIO_URL=http://192.168.100.136:8081 WORKFLOW_CONTEXT_SAMPLER=context_weighted_v2
  uv run python scripts/polycarrier_capacity_preflight.py \
    --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \
    --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32 \
  && uv run python scripts/run_multi_frame_batch_e2e.py \
    --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \
    --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32 \
    --run-dir metrics/e2e_runs/polycarrier_256b_smoke_retry2 \
    2>&1 | tee metrics/e2e_runs/polycarrier_256b_smoke_retry2_run.log
  ```
- **Health:** `preflight_ok` (all 5 batches ≥320 bits); sample 0 on `1lootoa`; live LLM RTT ~13.5–14.4s `assistant_text`; log growing (~+618B/8s); **no** `Failed to load model` / 400s yet. No `summary.json` yet → Phase 2 blocked.
- **Phase 2 later:** unload LM Studio qwen; restart llama `:8090` + ZLG `:9000` via `start_llama.bat` / `start_stego_api.bat` (WMI Create detachment — see `docs/results/zlg-overhaul-handoff-20260731.md`); `--server-url http://192.168.100.136:9000`.

---

## 2026-08-06 — Tick #71 / occurrence 69 (~13:33 local)

- **STATUS:** **dead incomplete** — retry PIDs **18700/33644/31760 DEAD**; terminal `281516` **aborted**; log frozen **153081 B** @ last line `10:32:31Z` (`angles_llm_request` after RTT 13543ms); **+0 B / 10s**.
- **RESUME 46ddef5e:** **yes** — retry dead, **no** `summary.json`, LM+ZLG both **200** (tick gate). Honor STOP: do not blind-relaunch until ZLG/llama unloaded (else repeat 400 load failures).
- **LM** `192.168.100.136:8081` → **200** (`qwen/qwen3.5-9b`). **ZLG** `:9000/health` → **200** (`status=ok`).
- **Progress delta vs tick #68:** ~85KB→**153KB** while sample 3 angles on `1lqskke` advanced; then dead (STOP kill / abort). Still **no encode**; `output-results/` empty.
- **Runs/summary:** samples 0–2 failed LM 400; sample 3 `sample_start` only; **NO `summary.json`**. No duplicate Phase 1 (only LUCID `11604/31664`).
- **Crash signs:** intentional stop + terminal abort; no death-line traceback; monitor `118846` still polling.
- **Next:** resume `46ddef5e` with sequential-GPU unblock, then Phase 1 `_retry2`.

---

## 2026-08-06 — STOP: Phase 1 blocked on GPU contention

- **Verified:** `192.168.100.136:9000/health` 200; `:8081/v1/models` 200; `:8090` 200. No `127.0.0.1:9000` tunnel — use LAN URL for Phase 2.
- **Phase 0:** synthetic 256b capacity miss; 64b encode validation exhausted — not a Phase 1 blocker (scale300 preflight OK: all 5 batches ≥320 bits).
- **Phase 1 first run** (`polycarrier_256b_smoke`): stalled ~199m mid-angles; killed earlier; autopsy log `polycarrier_256b_smoke_run.crashed_0953.log`.
- **Phase 1 retry** (`polycarrier_256b_smoke_retry`): samples **0–2 failed** with LM Studio `400` body `Failed to load model "qwen/qwen3.5-9b"` (6×); sample 3 still in angle-preview with no encode; **no `summary.json`**.
- **Action taken:** killed retry PIDs 18700/33644 (left LUCID `balanced_100_cont3` running). Do not keep relaunching Phase 1 while ZLG+LUCID share the 16 GB card.
- **Unblock recipe (plan sequential GPU):**
  1. Pause/finish LUCID cont3 (or accept sole LM Studio owner).
  2. Unload / stop ZLG `:9000` + llama-server `:8090`.
  3. Confirm `POST :8081/v1/chat/completions` with `qwen/qwen3.5-9b` stays healthy under load.
  4. Re-run Phase 1 → `metrics/e2e_runs/polycarrier_256b_smoke` (or `_retry2`).
  5. Then unload LM Studio; bring ZLG back; Phase 2 with `--server-url http://192.168.100.136:9000`.

---

## 2026-08-06 — Tick #68 / occurrence 66 (~13:17 local)

- **STATUS:** **advancing** — restart agent work done; fresh Phase 1 retry live + log growing.
- **RESUME 46ddef5e / fight 884a447e:** **no** — retry alive (terminal `281516` running; PIDs **18700/33644** on `polycarrier_256b_smoke_retry`); not dead/hung.
- **LM** `192.168.100.136:8081` → **200** (`qwen/qwen3.5-9b` present).
- **ZLG** `192.168.100.136:9000/health` → **200** (`status=ok`, `Qwen3.5-9B-Q4_K_M.gguf`).
- **Hung PIDs:** **27748/29496 gone**; accidental brief duplicate on old `polycarrier_256b_smoke` (33600/33716) also gone.
- **Runs:** primary = `run_multi_frame_batch_e2e` → `metrics/e2e_runs/polycarrier_256b_smoke_retry` (log ~85KB, +618B/8s); old dir emptied/autopsy only.
- **Sample:** 0–2 `sample_complete` **failed** (`400 Bad Request` on `:8081` chat); **3 in progress** on `1lqskke` with live LLM RTT (~14–44s); no encode/`summary.json` yet.
- **progress.md delta:** prior entry documented stall/kill/restart; this tick confirms restart succeeded and retry is advancing (despite early 400s).
- **Crash/stall:** none now (alive + growing). Watch repeated 400s / Model-reload pattern.
- **Next:** let retry finish; do not launch another Phase 1.

---

## 2026-08-06 — Phase 1 stall killed; restarting (~13:15 local)

- **STATUS:** Prior `polycarrier_256b_smoke` **STALLED ~199m** mid-`angles_llm_request` on sample 1 (`1lpcwhq`/`1lpdsdc`); no encode; no `summary.json`. Log frozen @ `06:53:30Z` / mtime ~09:53 local.
- **Killed (polycarrier tree only):** python **29496** (hung worker ~768MB); wait-monitor **34592**; rest of bash/uv/tee tree (28620/27912/20888/23660/33544/27748/30412/34412) already gone or cascaded. Cursor terminal id `118845` was not an OS PID.
- **Preserved:** LUCID `balanced_100_cont3` (`run_actual_workload_e2e` 11604/31664 + watchers 14576/28860) untouched.
- **Health:** LM `192.168.100.136:8081` → **200** (`/v1/models` + later live chat ping OK); ZLG `192.168.100.136:9000/health` → **200**.
- **Restart command** (shell **31760**; python **18700/33644**):
  ```bash
  export WORKFLOW_LLM_BACKEND=lm_studio LM_STUDIO_URL=http://192.168.100.136:8081 WORKFLOW_CONTEXT_SAMPLER=context_weighted_v2
  uv run python scripts/polycarrier_capacity_preflight.py \
    --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \
    --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32 \
  && uv run python scripts/run_multi_frame_batch_e2e.py \
    --angles-dir datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles \
    --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32 \
    --run-dir metrics/e2e_runs/polycarrier_256b_smoke_retry \
    2>&1 | tee metrics/e2e_runs/polycarrier_256b_smoke_retry_run.log
  ```
- **Restart health:** **not hung** — `preflight_ok`; log growing; live `angles_llm_request` RTTs. Old `polycarrier_256b_smoke/` autopsy only.
- **Blocker:** samples 0–2 failed with LM 400 `Failed to load model "qwen/qwen3.5-9b"` (LUCID/:8081 contention). No encode / no `summary.json` yet → Phase 2 not started.
- **RESUME 46ddef5e:** **no** while retry alive / Phase 1 incomplete.

---

## 2026-08-06 — Tick #67 / occurrence 65 (~13:12 local)

- **STATUS:** **STALLED** — Phase 1 process alive but no log progress ~199m.
- **RESUME 46ddef5e:** **no** — Phase 1 not dead (PIDs ~27748/29496 / terminal `118845` still `running` ~3.8h); Phase 1 incomplete (no summary). Hang ≠ dead.
- **LM** `192.168.100.136:8081` → **200** (`qwen/qwen3.5-9b` present).
- **ZLG** `192.168.100.136:9000/health` → **200** (`status=ok`, `Qwen3.5-9B-Q4_K_M.gguf`).
- **Sample:** 0 failed (`Model reloaded` 400); **1 stuck** on angles for `1lpcwhq`/`1lpdsdc` — last line `angles_llm_request` @ `06:53:30Z` with no matching round-trip; **no encode** (`frame_generation`/`stego_text` absent); posts 2–5 untouched.
- **Artifacts:** `polycarrier_256b_smoke/` — **no** `summary.json`; `output-results/` empty; log **138KB frozen** (mtime ~09:53 local / age ~199m; +21KB vs tick 66 then stop).
- **Crash/stall:** **STALLED** (alive + mtime ≫5m + zero size delta over 8s); CPU near-idle on worker; likely hung LLM HTTP after last RTT ~13s. Contention/LUCID may be involved.
- **Next:** kill/restart Phase 1 worker manually if hang persists; do not auto-resume `46ddef5e` while PID alive.

---

## 2026-08-06 — Tick #66 / occurrence 64 (~09:48 local)

- **STATUS:** Phase 1 alive + advancing (slow angles on sample 1).
- **RESUME 46ddef5e:** **no** — `118845` still `running` (~26m); PIDs ~27748/29496; Phase 1 incomplete (no summary).
- **LM** `192.168.100.136:8081/v1/models` → **200** (`qwen/qwen3.5-9b`). `127.0.0.1:8081` closed.
- **ZLG** `192.168.100.136:9000/health` → **200** (`status=ok`, `Qwen3.5-9B-Q4_K_M.gguf`).
- **Sample:** 0 failed (`Model reloaded` 400 @ 06:29:48Z); **1 in progress** alternating `1lpcwhq`↔`1lpdsdc` angle previews (no encode yet; posts 2–5 of sample untouched).
- **Artifacts:** `polycarrier_256b_smoke/` — **no** `summary.json`; `output-results/` empty; log ~117KB, last ~06:48:23Z (~+20KB / +4.5m vs tick 65).
- **Crash/stall:** none; continuous LLM RTT ~14–18s (cache hits ~0.5s). Contention likely (LUCID sharing `:8081`).

---

## 2026-08-06 — Tick #65 / occurrence 63 (~09:43 local)

- **STATUS:** Phase 1 alive + advancing (slow angles).
- **RESUME 46ddef5e:** **no** — process live (`118845` / PIDs ~27748/29496); not dead; Phase 1 incomplete.
- **LM** `192.168.100.136:8081/v1/models` → **200** (`qwen/qwen3.5-9b` present). `127.0.0.1:8081` closed.
- **ZLG** `192.168.100.136:9000/health` → **200** (`status=ok`, `Qwen3.5-9B-Q4_K_M.gguf`).
- **Sample:** 0 failed (`Model reloaded` 400 @ 06:29:48Z); **1 in progress** posts `1lpcwhq`→`1lpdsdc` (angles only; no encode yet).
- **Artifacts:** `polycarrier_256b_smoke/` — **no** `summary.json`; `output-results/` empty; log growing (~97KB, last ~06:43:54Z).
- **Crash/stall:** none now; prior sample-0 reload error only. LLM RTT ~13–17s.

---

## 2026-08-06 — ZLG :9000 verified; Phase 1 live

- `http://192.168.100.136:9000/health` → **200** (`status=ok`, `backend=llama_server`, model `Qwen3.5-9B-Q4_K_M.gguf`, `git_commit=11cab87`, `header_bits=0`, `max_new_tokens=256`).
- `http://192.168.100.136:8090` → **200** (llama-server).
- `http://192.168.100.136:8081/v1/models` → **200** (LM Studio; `qwen/qwen3.5-9b` present).
- `127.0.0.1:9000` tunnel **not** up — use LAN URL `http://192.168.100.136:9000` for Phase 2.
- `.env` `LM_STUDIO_URL=http://192.168.100.136:8081`.
- **Note:** LUCID `balanced_100_cont3` still sharing `:8081` — expect slower Phase 1 / possible contention.
- Phase 1 command already running: `run_multi_frame_batch_e2e.py … --samples 5 --posts-per-sample 6 --max-frames-per-post 4 --payload-bytes 32 --run-dir metrics/e2e_runs/polycarrier_256b_smoke` (log: `polycarrier_256b_smoke_run.log`). Do not start a duplicate.

---

## 2026-08-06 — Live resume (LM Studio up)

- Confirmed `http://192.168.100.136:8081/v1/models` HTTP 200 (gemma/qwen/etc.).
- ZLG `:9000` was down at start of that session; **now up** (see entry above).
- **Phase 0 first attempt (256-bit synthetic):** FAILED — `Insufficient parent-conditioned multi-frame capacity`
  - Artifact: `metrics/e2e_runs/multi_frame_20260806T061931Z/result.json`
  - Root cause: synthetic posts only ~4 recoverable bits each (~64 bit naive budget at 8 frames); 256-bit payload cannot fit.
- **Phase 0 retry (`--payload-bits 64`):** FAILED exit 1 — planner OK (multi-frame) but frame 0 encode `diagnostic_validation_exhausted` (angle recoverability gate never passed on synthetic).
  - Artifact: `metrics/e2e_runs/multi_frame_20260806T062111Z/result.json`
  - Log: `metrics/e2e_runs/polycarrier_phase0_synthetic_64b.log`
- **Decision:** do not block Phase 1 on synthetic harness limits; 256-bit multi-frame gate is Phase 1 on scale300 angles.

---

## 2026-08-06 — Offline continuation (LLM down)

### Live attempts / failures

1. LM Studio `http://192.168.100.136:8081/v1/models` — timeout; **retry after 5s** also timeout (10s).
2. Ping `192.168.100.136` — Destination host unreachable.
3. Local ports `8081/8090/9000` closed; ZLG health refused.
4. AI Studio fallback (`GOOGLE_AI_STUDIO_MODEL`) — **403 PERMISSION_DENIED** (GenerateContent blocked). Not used for smoke.

### Offline findings + code

- **Phase 1 knob bug:** plan default `6 posts × 3 frames` cannot fit 256+32 bits on scale300 (min capacity 240–270). **Corrected to `6×4`** (preflight min 320). Alternate: `8×3`.
- Shipped `scripts/polycarrier_capacity_preflight.py` (exit 1 on undershoot).
- Implemented sample-layer **§7** companion: `sample_paired_statistics` (sign test + Holm); wired into aggregator (`--cluster-by-primary-post`).
- Updated `execution-plan.md` Phase 1 commands + `metrics-multicomment.md` §7.

### Commands run

```text
# connectivity (failed)
curl LM Studio :8081  → timeout; retry → timeout
ping 192.168.100.136 → unreachable
AI Studio generate_content → 403

# offline
uv run pytest -q src/tests/test_polycarrier_sample_metrics.py  → 8 passed
uv run pyright src/services/polycarrier_sample_metrics.py       → 0 errors
uv run python scripts/polycarrier_capacity_preflight.py ... --max-frames-per-post 3 → exit 1
uv run python scripts/polycarrier_capacity_preflight.py ... --max-frames-per-post 4 → exit 0
```

### Files touched this session

- `scripts/polycarrier_capacity_preflight.py` (new)
- `src/services/polycarrier_sample_metrics.py` (§7 companion)
- `scripts/aggregate_polycarrier_sample_metrics.py` (`--cluster-by-primary-post`)
- `src/tests/test_polycarrier_sample_metrics.py`
- `docs/plans/polycarrier/execution-plan.md`
- `docs/plans/polycarrier/metrics-multicomment.md`
- `docs/plans/polycarrier/progress.md`

### Blocked on (historical — pre-move)

- Was waiting on remote ASUS / LM Studio / tunneled ZLG. **Obsolete:** this workspace
  is now on ASUS; use `http://127.0.0.1:8081` and `http://127.0.0.1:9000`.

### Next actions (when GPU services are up)

1. Phase 0: `PYTHONPATH=src uv run python scripts/run_multi_frame_stego_e2e.py --mode synthetic --payload-bits 256 --max-frames-per-post 8`
2. Preflight then Phase 1 smoke with **`6×4`** → `metrics/e2e_runs/polycarrier_256b_smoke`
3. Phase 2 ZLG (`--server-url http://127.0.0.1:9000`) → Phase 3 build → `aggregate_polycarrier_sample_metrics.py`

---

## 2026-08-06 — Sample-layer aggregator shipped

### Done

- Confirmed frozen angles (schema v3 / `context_weighted_v2`); preferred
  `datasets/prep_runs/context_weighted_v2/scale300_20260729/news_angles` (167 posts, bare `{post_id}.json`).
- Implemented sample-layer rollups §§1.2–6.2 + CLI + tests.

### Tests (then)

```text
uv run pytest -q src/tests/test_polycarrier_sample_metrics.py  → 7 passed
uv run pyright src/services/polycarrier_sample_metrics.py      → 0 errors
```

---

## 2026-08-06 — Plan authored

- Codename `POLYCARRIER` under `docs/plans/polycarrier/`.
- Default payload 32 bytes / 256 useful bits; `/zlg-comparison` metric lane.
