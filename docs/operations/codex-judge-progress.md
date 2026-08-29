# Codex / Claude Judge progress and resume

The five LLM-judge metrics write all artifacts under a comparison run's
`comparison_dataset/codex_judgments/` directory. They are designed for an
interrupted local/overnight run: judgments are append-only and a completed
`task_id` is never sent again.

Default backend is **Claude Code** (`--backend claude`, model `sonnet`, the
Claude CLI alias for the current Sonnet 5 generation).
Codex remains available via `--backend codex` (model `gpt-5.6-luna`).

## Monitoring

Each metric writes `<metric>_progress.json` after every completed judge call.
It contains `total_tasks`, `completed`, `pending`, `errors`, and `complete`.
The JSONL file is flushed after every row, so progress survives process loss.

For a 50-pair pilot, expected task counts are 100 standout, 50 weak-link, and
150 each for suspicion, attribution, and register (600 total). A pilot is
healthy only when valid coverage is at least 95%, answer positions show no
obvious index bias, and raw output contains no repeated schema or prompt-leak
failures.

## Commands

The preferred automation entry point runs all five metrics, scores them, audits
the pilot, and only then continues to the full run:

```powershell
uv run python scripts\run_codex_judge_campaign.py --run-dir $Run --phase auto --pilot-limit 50 --max-workers 4
```

Switch back to Codex Luna when credits allow:

```powershell
uv run python scripts\run_codex_judge_campaign.py --run-dir $Run --phase auto --backend codex --model gpt-5.6-luna
```

Use `--phase pilot` to stop after the audit or `--phase full` to resume a
previously approved pilot. Every operation is idempotent and uses the same
append-only cache.

Run each metric independently; re-running the same command resumes it:

```powershell
$Run = 'metrics\zlg_comparison_runs\zlg_batch_scale300'
uv run python scripts\run_codex_judge.py --metric suspicion --run-dir $Run --limit 50 --max-workers 4
uv run python scripts\score_codex_judgments.py --metric suspicion --run-dir $Run
```

After the pilot passes, omit `--limit` to evaluate every paired row. Keep the
same backend, model, reasoning effort, prompts, and schemas: these fields are
included in `task_id`, so a changed evaluation configuration creates a distinct
cache.

For unattended work, gate the full run on the pilot progress files: require
every metric's `complete` field to be true and every `errors` field to be zero.
The full commands may safely reuse the same run directory: the first 50-pair
judgments are recognized by `task_id` and only missing tasks are submitted.

The results viewer's `/zlg-comparison` LLM Judge section reads the summary,
progress, and JSONL artifacts directly. Refresh the page to see newly flushed
rows.

Before expanding a pilot, run `uv run python scripts/audit_codex_judge_pilot.py
--run-dir <run>`. It writes `pilot_audit.json` and fails closed on incomplete
coverage, errors, or an extreme positional-response collapse.
