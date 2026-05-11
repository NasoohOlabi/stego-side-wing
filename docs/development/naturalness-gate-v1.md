# Naturalness Gate V1

## Purpose

The naturalness gate is an experiment for reducing topically wrong or fragment-like stego comments without changing workflow or system prompts.

The experiment is isolated under:

```text
metrics/experiments/naturalness_gate_v1/
```

Run artifacts stay in that tree:

- `runs/<run_id>/<variant>/dataset/`
- `runs/<run_id>/<variant>/input-angles/`
- `runs/<run_id>/<variant>/output-results/`
- `runs/<run_id>/<variant>/metrics/`
- `runs/<run_id>/<variant>/failures/`
- `reviews/`
- `logs/`

Benchmark comparisons must not read from global `output-results` or unrelated `metrics/e2e_runs` folders.

## What Was Added

The code adds two checks:

1. Angle relevance scoring after angle generation.
2. Comment plausibility validation during sender candidate validation.

The angle score compares each angle against the post title, selftext, and comment context. It records:

- overlap tokens
- source quote fragment warnings
- weak post relevance warnings
- kept and rejected counts
- reason counts

The comment plausibility validator rejects:

- empty comments
- title-like fragments
- very short noun phrases
- broken quotes
- repeated canned suffixes
- low-content comments

## Benchmark Result

Clean tuned run:

```text
metrics/experiments/naturalness_gate_v1/runs/tuned_clean_20260511T115S
```

Results:

| Lane | Success | Failures | Receiver Success On Accepted | KL | JSD | Perplexity |
|---|---:|---:|---:|---:|---:|---:|
| `balanced` | 98 / 100 | 2 | 1.0 | 7.2611 | 0.5669 | 65.8243 |
| `balanced_naturalness_gate` | 86 / 100 | 14 | 1.0 | 7.1539 | 0.5612 | 68.6992 |

The gated lane slightly improved KL/JSD, but it hurt embedding rate too much.

Failure breakdown:

| Lane | Failure |
|---|---:|
| `balanced` receiver angle mismatch | 2 |
| `balanced_naturalness_gate` receiver angle mismatch | 13 |
| `balanced_naturalness_gate` invalid JSON | 1 |

Gate activity in the gated lane:

| Checked | Kept | Rejected |
|---:|---:|---:|
| 49,093 | 48,307 | 786 |

The important conclusion is that the first strict gate was not mainly failing by rejecting too many total angles. It was failing because removing even a small number of angle options can change which angles the sender works with and can increase angle mismatch failures.

## Settled Middle Ground

The default enabled mode is now `middle`.

```text
WORKFLOW_NATURALNESS_GATE_ENABLED=1
WORKFLOW_NATURALNESS_GATE_MODE=middle
```

Middle mode:

- hard-rejects only clearly off-topic angles with no post/comment overlap;
- records source quote fragments as warnings;
- does not hard-reject a fragment if the angle is still anchored to the post topic;
- keeps the comment plausibility validator active for generated stego candidates.

Strict mode remains available for experiments:

```text
WORKFLOW_NATURALNESS_GATE_MODE=strict
```

Report-only mode is available when measuring how many angles would be flagged without changing selection:

```text
WORKFLOW_NATURALNESS_GATE_MODE=report
```

## Recommendation

Do not use a strict angle filter as the default. The better default is:

1. Keep enough angles for receiver stability.
2. Hard-block only obviously unrelated angles.
3. Reject bad visible comments at candidate-validation time.
4. Track naturalness as a benchmark dimension, not as an unbounded filter.

This preserves the embedding rate better while still addressing the topically wrong Starbucks/Ozzy/vaccine-style failures.

## Commands

Focused tests:

```powershell
uv run pytest -q src\tests\test_naturalness_gate.py src\tests\test_pipeline_stego.py src\tests\test_run_actual_workload_e2e.py
```

Full validation:

```powershell
uv run pytest -q
uv run pyright
```

Pilot experiment:

```powershell
uv run python scripts\run_naturalness_gate_experiment.py --samples 100 --run-name pilot_001
```

