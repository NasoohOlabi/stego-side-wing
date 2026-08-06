"""Incrementally enrich existing paired rows with reference-based quality metrics."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from build_zlg_method_comparison_dataset import (  # noqa: E402
    _clustered_method_summary,  # pyright: ignore[reportPrivateUsage]
    _clustered_paired_stats,  # pyright: ignore[reportPrivateUsage]
    _reference_and_context,  # pyright: ignore[reportPrivateUsage]
    _row_level_paired_stats,  # pyright: ignore[reportPrivateUsage]
    _summary,  # pyright: ignore[reportPrivateUsage]
)

from services.paired_quality_metrics_service import (  # noqa: E402
    score_bertscore_pairs,
    score_reference_metrics,
    score_self_consistency,
)


def _load(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _save(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )


def _refresh_summary(rows_path: Path, rows: list[dict[str, Any]]) -> bool:
    summary_path = rows_path.with_name("summary.json")
    if not summary_path.is_file():
        return False
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["methods"] = _summary(rows)
    summary["methods_clustered_by_post"] = _clustered_method_summary(rows)
    summary["row_level_descriptive_statistics"] = _row_level_paired_stats(rows)
    summary["paired_statistics"] = _clustered_paired_stats(rows)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _dataset_file(row: dict[str, Any], dataset_dir: Path | None) -> Path:
    if dataset_dir is not None:
        return dataset_dir / f"{row.get('post_id')}.json"
    source = Path(str(row.get("source_output_file") or ""))
    return source.parent.parent / "dataset" / f"{row.get('post_id')}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="comparison_dataset/paired_rows.jsonl")
    parser.add_argument("--dataset-dir")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--bertscore-model", default="roberta-large")
    parser.add_argument("--bertscore-num-layers", type=int)
    parser.add_argument("--bertscore-batch-size", type=int, default=16)
    parser.add_argument("--checkpoint-every", type=int, default=5)
    args = parser.parse_args()
    rows_path = Path(args.input)
    rows = _load(rows_path)
    dataset_dir = Path(args.dataset_dir) if args.dataset_dir else None
    contexts: dict[str, tuple[str, str]] = {}
    scored = 0
    for row in rows:
        if row.get("bleu") is not None and row.get("bertscore_f1") is not None:
            continue
        post_id = str(row.get("post_id"))
        if post_id not in contexts:
            contexts[post_id] = _reference_and_context(_dataset_file(row, dataset_dir))
        reference_text, thread_context = contexts[post_id]
        row["reference_text"] = reference_text
        row["thread_context"] = thread_context
        result = score_reference_metrics(
            str(row.get("stegotext") or ""),
            reference_text,
            bertscore_model=args.bertscore_model,
            bertscore_num_layers=args.bertscore_num_layers,
            device=args.device,
            include_bertscore=False,
        )
        row.update(result)
        scored += 1
        if scored % args.checkpoint_every == 0:
            _save(rows_path, rows)
    pending = [row for row in rows if row.get("bertscore_f1") is None and row.get("reference_text")]
    values, warning, provenance = score_bertscore_pairs(
        [str(row.get("stegotext") or "") for row in pending],
        [str(row.get("reference_text") or "") for row in pending],
        model_name=args.bertscore_model,
        num_layers=args.bertscore_num_layers,
        device=args.device,
        batch_size=args.bertscore_batch_size,
    )
    if values is not None:
        for row, value in zip(pending, values, strict=True):
            row.update(value)
            row.setdefault("quality_metric_provenance", {})["bertscore"] = provenance
    elif warning:
        for row in pending:
            row.setdefault("quality_metric_warnings", []).append(warning)
    _save(rows_path, rows)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row.get("post_id")), str(row.get("method")))].append(row)
    for group in groups.values():
        texts = [str(row.get("stegotext") or "") for row in group]
        for row in group:
            score, warning, provenance = score_self_consistency(
                str(row.get("stegotext") or ""), texts, device=args.device
            )
            row["self_consistency"] = score
            warnings = row.setdefault("quality_metric_warnings", [])
            if warning and warning not in warnings:
                warnings.append(warning)
            if provenance:
                row.setdefault("quality_metric_provenance", {})["self_consistency"] = provenance
    _save(rows_path, rows)
    summary_refreshed = _refresh_summary(rows_path, rows)
    print(
        json.dumps(
            {
                "rows": len(rows),
                "reference_rows_scored": scored,
                "output": str(rows_path),
                "summary_refreshed": summary_refreshed,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
