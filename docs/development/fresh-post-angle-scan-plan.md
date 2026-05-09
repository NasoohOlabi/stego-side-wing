# Fresh Post Angle-Scan Stego Diagnostic

## Summary

This diagnostic runs one fresh local post through `data_load -> research -> genAngles -> stego`, then forces one binary selection payload per generated angle. It is intended to measure whether research and angle generation produce angles that can actually be turned into plausible, decodable `stegoText`.

The diagnostic path skips payload protection and compression. Production `StegoPipeline.encode()` behavior is unchanged.

## Flow

1. Select a post from `datasets/news_cleaned` that was not used by the latest `metrics/e2e_runs/*/post_ids.json`, unless `--post-id` is provided.
2. Load URL/selftext with `DataLoadPipeline.preview_post`; fall back to the local dataset if fetch fails.
3. Run `ResearchPipeline.preview_post(force=True)`.
4. Run `GenAnglesPipeline.preview_post(allow_fallback=True)` in `extractive_zero_kld` mode by default; use `--angles-mode configured` only when explicitly testing the configured analyzer.
5. Compute research/angle alignment from token overlap between post/comment/search text and angle category/tangent/source quote.
6. If angle alignment is clearly low, rerun `genAngles` once from the researched post with any existing `angles` removed.
7. Flatten all angles and scan each angle by constructing `comment_bits + angle_bits_for_index(idx, n_angles)`.
8. Call `StegoPipeline.encode_binary_selection_bits()` for each angle and write one JSONL row per attempt.

## Metrics

Artifacts are written under `metrics/angle_scan_runs/<run_id>/`:

- `stage_reports.json`: data load, research, genAngles reports, hashes, and whether genAngles was repeated.
- `research_angle_alignment.json`: post/search/angle token overlap, likely-mismatch flag, best and worst angle examples.
- `angle_scan.jsonl`: per-angle result including selected angle, binary bits, generated `stegoText`, decoded angle, contextuality gate, rejection reasons, and error.
- `angle_scan_summary.json`: success rate, JSON failure rate, decode mismatch rate, contextuality rejection rate, top bad categories, and examples.

## Known Failure Mode

The current feedback run showed cases where research was correct but generated angles were unrelated to the post. Example: a Kentucky organ-harvesting post produced many Starbucks/business angles. That makes `stegoText` generation intrinsically hard because the model must write a comment that fits one context while semantically pointing to another.

This diagnostic separates that upstream angle-quality problem from downstream stego-generation failures.

## Usage

```powershell
uv run python scripts/run_fresh_post_angle_scan.py --llm-backend qwen
```

Useful options:

- `--post-id <id>`: scan a specific local post.
- `--limit-angles <n>`: run a small pilot before scanning every angle.
- `--resume`: continue an interrupted run without repeating completed angle rows.
- `--comment-index <n>`: force a comment-chain context instead of post-level context.
- `--max-retries <n>`: retry stego generation per angle.
- `--angles-mode configured`: opt into the configured angle analyzer path; default is `extractive_zero_kld` because the configured analyzer can hang before GPU-backed stego work begins.
