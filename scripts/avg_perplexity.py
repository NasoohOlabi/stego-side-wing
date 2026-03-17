import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _progress_update_every(total: int) -> int:
    return max(1, total // 120)


def render_progress(label: str, current: int, total: int, update_every: int) -> None:
    if total <= 0:
        return
    if current != total and (current % update_every != 0):
        return
    width = 28
    filled = int((current / total) * width)
    bar = "#" * filled + "-" * (width - filled)
    print(
        f"\r{label}: [{bar}] {current}/{total} ({(current / total) * 100:5.1f}%)",
        end="",
        flush=True,
    )
    if current == total:
        print()


def extract_stego_text(data: Any) -> str | None:
    if isinstance(data, dict):
        stego = data.get("stego_text")
        if isinstance(stego, str):
            return stego
        stego = data.get("stegoText")
        if isinstance(stego, str):
            return stego
        return None

    if isinstance(data, list) and data:
        first_item = data[0]
        if isinstance(first_item, dict):
            stego = first_item.get("stegoText")
            if isinstance(stego, str):
                return stego
    return None


def _json_number(value: float) -> float | None:
    return value if math.isfinite(value) else None


def save_metrics_report(metrics_dir: Path, report: dict[str, Any]) -> Path:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = metrics_dir / f"perplexity_metrics_{timestamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report_path


def resolve_device(torch_module: Any, device_arg: str) -> str:
    if device_arg != "auto":
        return device_arg
    return "cuda" if torch_module.cuda.is_available() else "cpu"


def compute_text_perplexity(
    tokenizer: Any,
    model: Any,
    torch_module: Any,
    text: str,
    stride: int,
    max_length: int,
    device: str,
) -> float:
    encoded = tokenizer(text, return_tensors="pt", truncation=False)
    input_ids = encoded["input_ids"].to(device)
    seq_len = input_ids.size(1)
    if seq_len < 2:
        return math.nan

    total_nll = 0.0
    total_tokens = 0
    for i in range(0, seq_len, stride):
        begin_loc = max(i + stride - max_length, 0)
        end_loc = min(i + stride, seq_len)
        target_len = end_loc - i
        if target_len <= 0:
            continue

        chunk_ids = input_ids[:, begin_loc:end_loc]
        target_ids = chunk_ids.clone()
        target_ids[:, :-target_len] = -100

        with torch_module.no_grad():
            outputs = model(chunk_ids, labels=target_ids)
            loss = float(outputs.loss.item())

        total_nll += loss * target_len
        total_tokens += target_len

        if end_loc >= seq_len:
            break

    if total_tokens <= 0:
        return math.nan
    return float(math.exp(total_nll / total_tokens))


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
        default=r"D:\Master\code\stego-results-viewer\metrics",
        help="Directory where each run stores a JSON metrics report.",
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

    if args.stride <= 0:
        raise SystemExit("--stride must be a positive integer.")

    output_dir = Path(args.output_dir)
    metrics_dir = Path(args.metrics_dir)

    output_files = sorted(output_dir.glob("*.json"))
    if not output_files:
        raise SystemExit(f"No output files found in: {output_dir}")

    valid_entries: list[tuple[Path, str]] = []
    skipped_invalid_json = 0
    skipped_missing_stego = 0
    skipped_empty_text = 0

    update_every = _progress_update_every(len(output_files))
    for idx, file_path in enumerate(output_files, start=1):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            skipped_invalid_json += 1
            render_progress("Reading output files", idx, len(output_files), update_every)
            continue

        stego_text = extract_stego_text(data)
        if stego_text is None:
            skipped_missing_stego += 1
            render_progress("Reading output files", idx, len(output_files), update_every)
            continue

        if not stego_text.strip():
            skipped_empty_text += 1
            render_progress("Reading output files", idx, len(output_files), update_every)
            continue

        valid_entries.append((file_path, stego_text))
        render_progress("Reading output files", idx, len(output_files), update_every)

    if not valid_entries:
        raise SystemExit("No valid stego texts were found; cannot compute perplexity.")

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency for perplexity evaluation. Install transformers and torch."
        ) from exc

    device = resolve_device(torch, args.device)
    print("Loading language model...")
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForCausalLM.from_pretrained(args.model_name)
    model.to(device)
    model.eval()
    max_length = int(getattr(model.config, "n_positions", 1024))

    perplexities: list[float] = []
    file_scores: list[dict[str, Any]] = []
    ppl_update_every = _progress_update_every(len(valid_entries))
    for idx, (file_path, stego_text) in enumerate(valid_entries, start=1):
        ppl = compute_text_perplexity(
            tokenizer=tokenizer,
            model=model,
            torch_module=torch,
            text=stego_text,
            stride=args.stride,
            max_length=max_length,
            device=device,
        )
        if not math.isfinite(ppl):
            render_progress("Computing perplexity", idx, len(valid_entries), ppl_update_every)
            continue
        perplexities.append(ppl)
        file_scores.append({"file": str(file_path), "perplexity": ppl})
        render_progress("Computing perplexity", idx, len(valid_entries), ppl_update_every)

    if not perplexities:
        raise SystemExit("Perplexity computation produced no valid scores.")

    avg_perplexity = sum(perplexities) / len(perplexities)
    min_perplexity = min(perplexities)
    max_perplexity = max(perplexities)

    print("=== GPT-2 Perplexity Report ===")
    print(f"Model: {args.model_name}")
    print(f"Device: {device}")
    print(f"Stride: {args.stride}")
    print()
    print("=== Dataset Summary ===")
    print(f"Total output files: {len(output_files)}")
    print(f"Usable stego texts: {len(valid_entries)}")
    print(f"Skipped (invalid JSON): {skipped_invalid_json}")
    print(f"Skipped (missing stego text): {skipped_missing_stego}")
    print(f"Skipped (empty stego text): {skipped_empty_text}")
    print()
    print("=== Perplexity Summary ===")
    print(f"Scored texts: {len(perplexities)}")
    print(f"Average perplexity: {avg_perplexity:.12f}")
    print(f"Minimum perplexity: {min_perplexity:.12f}")
    print(f"Maximum perplexity: {max_perplexity:.12f}")

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "output_dir": str(output_dir.resolve()),
            "metrics_dir": str(metrics_dir.resolve()),
            "model_name": args.model_name,
            "device": device,
            "stride": args.stride,
            "max_length": max_length,
        },
        "dataset_summary": {
            "total_output_files": len(output_files),
            "usable_stego_texts": len(valid_entries),
            "skipped_invalid_json": skipped_invalid_json,
            "skipped_missing_stego_text": skipped_missing_stego,
            "skipped_empty_stego_text": skipped_empty_text,
            "scored_texts": len(perplexities),
        },
        "perplexity_summary": {
            "average_perplexity": _json_number(avg_perplexity),
            "min_perplexity": _json_number(min_perplexity),
            "max_perplexity": _json_number(max_perplexity),
        },
        "per_file_perplexity": file_scores,
    }
    report_path = save_metrics_report(metrics_dir, report)
    print()
    print(f"Saved metrics JSON: {report_path}")


if __name__ == "__main__":
    main()
