import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.config import METRICS_DIR  # noqa: E402
from services.stego_metrics_service import (  # noqa: E402
    TOKEN_RE,
    metrics_cli_progress,
    run_divergence_metrics,
)


def _fmt_metric(value: float | None) -> str:
    if value is None:
        return "nan"
    return f"{float(value):.12f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute KL and JSD of stego texts against primary and secondary baselines."
    )
    parser.add_argument(
        "--output-dir",
        default="output-results",
        help="Directory with generated output JSON files.",
    )
    parser.add_argument(
        "--dataset-dir",
        default="datasets/news_cleaned",
        help="Directory with original post JSON files.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=1e-6,
        help="Additive smoothing constant.",
    )
    parser.add_argument(
        "--metrics-dir",
        default=str(METRICS_DIR),
        help="Directory where each run stores a JSON metrics report (default: <repo>/metrics).",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    dataset_dir = Path(args.dataset_dir)
    metrics_dir = Path(args.metrics_dir)

    result = run_divergence_metrics(
        output_dir,
        dataset_dir,
        metrics_dir,
        alpha=args.alpha,
        progress_hook=metrics_cli_progress,
    )
    report = result["report"]
    ds = report["dataset_summary"]
    primary = report["primary_baseline_matched_post"]
    secondary = report["secondary_baseline_global_corpus"]
    print("=== Divergence Report ===")
    print(f"Tokenization: word unigram ({TOKEN_RE.pattern})")
    print(f"Smoothing alpha: {args.alpha}")
    print("KL direction: KL(stego || baseline)")
    print()
    print("=== Dataset Summary ===")
    print(f"Total output files: {ds['total_output_files']}")
    print(f"Unique output post IDs: {ds['unique_output_post_ids']}")
    print(f"Usable stego samples: {ds['usable_stego_samples']}")
    print(f"Skipped (missing stegoText): {ds['skipped_missing_stegoText']}")
    print(f"Skipped (empty stegoText tokens): {ds['skipped_empty_stego_tokens']}")
    print(f"Skipped (missing primary post file): {ds['skipped_missing_primary_post_file']}")
    print(f"Skipped (primary post has no non-empty comment bodies): {ds['skipped_empty_primary_comment_bodies']}")
    print()
    print("=== Primary Baseline (matched-post comments) ===")
    print(f"Comparisons: {primary['comparisons']}")
    print(f"Posts represented: {primary['stego_posts_represented']}")
    print(f"Average KL(stego || matched_post): {_fmt_metric(primary['average_kl_stego_vs_matched_post'])}")
    print(f"Average JSD(stego, matched_post): {_fmt_metric(primary['average_jsd_stego_vs_matched_post'])}")
    print()
    print("=== Secondary Baseline (global comments corpus) ===")
    print(f"Global source post files: {ds['global_source_post_files']}")
    print(f"Global non-empty comment bodies: {ds['global_nonempty_comment_bodies']}")
    print(f"Comparisons: {secondary['comparisons']}")
    print(f"Stego posts represented: {secondary['stego_posts_represented']}")
    print(f"Average KL(stego || global_corpus): {_fmt_metric(secondary['average_kl_stego_vs_global_corpus'])}")
    print(f"Average JSD(stego, global_corpus): {_fmt_metric(secondary['average_jsd_stego_vs_global_corpus'])}")
    print()
    print(f"Saved metrics JSON: {result['report_path']}")


if __name__ == "__main__":
    main()
