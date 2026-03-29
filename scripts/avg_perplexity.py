import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.config import METRICS_DIR  # noqa: E402
from services.stego_metrics_service import (  # noqa: E402
    metrics_cli_progress,
    run_perplexity_metrics,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute average GPT-style perplexity across stego texts in output JSON files."
    )
    parser.add_argument(
        "--output-dir",
        default="output-results",
        help="Directory with generated output JSON files.",
    )
    parser.add_argument(
        "--metrics-dir",
        default=str(METRICS_DIR),
        help="Directory where each run stores a JSON metrics report (default: <repo>/metrics).",
    )
    parser.add_argument(
        "--model-name",
        default="gpt2",
        help="Hugging Face model name for causal language modeling.",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=512,
        help="Sliding window stride in tokens when computing perplexity.",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="Computation device; 'auto' selects CUDA when available.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    metrics_dir = Path(args.metrics_dir)

    result = run_perplexity_metrics(
        output_dir,
        metrics_dir,
        model_name=args.model_name,
        stride=args.stride,
        device=args.device,
        progress_hook=metrics_cli_progress,
    )
    report = result["report"]
    ds = report["dataset_summary"]
    ps = report["perplexity_summary"]
    print("=== GPT-2 Perplexity Report ===")
    print(f"Model: {args.model_name}")
    print(f"Device: {report['config']['device']}")
    print(f"Stride: {args.stride}")
    print()
    print("=== Dataset Summary ===")
    print(f"Total output files: {ds['total_output_files']}")
    print(f"Usable stego texts: {ds['usable_stego_texts']}")
    print(f"Skipped (invalid JSON): {ds['skipped_invalid_json']}")
    print(f"Skipped (missing stego text): {ds['skipped_missing_stego_text']}")
    print(f"Skipped (empty stego text): {ds['skipped_empty_stego_text']}")
    print()
    print("=== Perplexity Summary ===")
    print(f"Scored texts: {ds['scored_texts']}")
    print(f"Average perplexity: {ps['average_perplexity']:.12f}")
    print(f"Minimum perplexity: {ps['min_perplexity']:.12f}")
    print(f"Maximum perplexity: {ps['max_perplexity']:.12f}")
    print()
    print(f"Saved metrics JSON: {result['report_path']}")


if __name__ == "__main__":
    main()
