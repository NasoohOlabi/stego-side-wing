# Stego Feedback Loop

## Goal

Improve strict sender/receiver recovery on fresh real posts. Primary metric is `samples_succeeded / requested_samples` under receiver decode validation. Capacity can be sacrificed if reliability improves.

## Baseline

- Prior 200-sample ablation: `qwen/qwen3.5-9b` succeeded `92/200`; `openai/gpt-oss-20b` succeeded `40/200`.
- Main failure mode: strict decode validation did not recover the selected hidden angle.
- Qwen is the default LM Studio model after baseline commit `211e7de`.

## Experiment Policy

- Use dedicated branch `codex/stego-qwen-feedback-loop`.
- Commit each implemented path before comparing the next path.
- Avoid prompt edits unless isolated in a prompt-only commit.
- Use fresh non-reused posts for supervised runs. If the dataset cannot supply enough unique posts, reduce the sample count instead of cycling posts.

## Iteration 1: Qwen Default + Lower Balanced Capacity

Hypothesis: strict recovery improves when the sender generates candidates for fewer angle prompts and spends retries on the selected angle neighborhood.

Changes:

- Default workflow backend changed from Google AI Studio to LM Studio.
- Default LM Studio model remains `qwen/qwen3.5-9b`.
- Balanced profile `WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT` default changed from `4` to `2`.
- Balanced profile default max retries changed from `4` to `6`.

Validation plan:

- Unit tests: `uv run pytest -q src/tests/test_workflow_llm_backend_config.py src/tests/test_model_naturalness_ablation.py src/tests/test_pipeline_stego.py`.
- Type check: `uv run pyright`.
- Fresh supervised run: `uv run python scripts/run_actual_workload_e2e.py --variant balanced --samples-per-profile 20 --max-retries 6 --run-dir metrics/stego_feedback_loop/iter1_qwen_low_capacity --overwrite`.

Decision rule:

- Keep if fresh-run success rate beats `46%` Qwen baseline materially.
- If gains are weak, next branch should test selected-angle-only generation or angle separability ranking before generation.

Result:

- Run: `metrics/stego_feedback_loop/iter1_qwen_low_capacity`.
- Fresh posts: `20`, no post reuse.
- Success: `11/20` (`55%`).
- Failures: `9/20`, all `Decoding validation failed`.
- Quality on successes: matched-post JSD `0.5622`, perplexity `85.26`.
- Decision: improvement is real but insufficient. Continue with selected-angle-only generation to reduce decoder confusion and LLM call volume.

## Iteration 2: Selected-Angle-Only Generation

Hypothesis: generation should spend all attempts on the hidden selected angle. Prompting alternate angle groups may add weak few-shot examples and increase semantic drift.

Planned change:

- Balanced profile `WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT`: `2` to `1`.
- Keep Qwen and max retries `6`.

Validation plan:

- Unit tests: same targeted suite as Iteration 1.
- Fresh supervised run: `uv run python scripts/run_actual_workload_e2e.py --variant balanced --samples-per-profile 20 --max-retries 6 --run-dir metrics/stego_feedback_loop/iter2_selected_angle_only --overwrite`.
