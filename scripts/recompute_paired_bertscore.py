#!/usr/bin/env python3
"""Force-rescore BERTScore on an existing comparison_dataset after scorer kwargs change.

Recomputes ``bertscore_precision`` / ``bertscore_recall`` / ``bertscore_f1`` only
(using ``rescale_with_baseline=True`` via ``score_bertscore_pairs``). Leaves BLEU,
ROUGE, perplexity, KL/JSD, and capacity fields untouched.

Example::

    uv run python scripts/recompute_paired_bertscore.py \\
      --input metrics/zlg_comparison_runs/zlg_batch_scale300/comparison_dataset/paired_rows.jsonl \\
      --device cpu
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import build_zlg_method_comparison_dataset as build  # noqa: E402

from services.paired_quality_metrics_service import score_bertscore_pairs  # noqa: E402

_KEYS = ("bertscore_precision", "bertscore_recall", "bertscore_f1")


def _load(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _means(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    by_method: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_method.setdefault(str(row.get("method")), []).append(row)
    for method, group in by_method.items():
        block: dict[str, float] = {}
        for key in _KEYS:
            vals = [
                float(row[key])
                for row in group
                if isinstance(row.get(key), (int, float))
            ]
            if vals:
                block[key] = statistics.fmean(vals)
        out[method] = block
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="comparison_dataset/paired_rows.jsonl")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--bertscore-model", default="roberta-large")
    parser.add_argument("--bertscore-num-layers", type=int)
    parser.add_argument("--bertscore-batch-size", type=int, default=16)
    args = parser.parse_args()

    rows_path = Path(args.input).resolve()
    rows = _load(rows_path)
    missing_ref = [i for i, row in enumerate(rows) if not str(row.get("reference_text") or "").strip()]
    if missing_ref:
        raise SystemExit(
            f"{len(missing_ref)} rows lack reference_text; cannot rescore without rebuilding "
            "references (pass rows that already carry reference_text)."
        )

    before = _means(rows)
    values, warning, provenance = score_bertscore_pairs(
        [str(row.get("stegotext") or "") for row in rows],
        [str(row.get("reference_text") or "") for row in rows],
        model_name=args.bertscore_model,
        num_layers=args.bertscore_num_layers,
        device=args.device,
        batch_size=args.bertscore_batch_size,
    )
    if values is None:
        raise SystemExit(warning or "BERTScore unavailable")

    for row, value in zip(rows, values, strict=True):
        row.update(value)
        row.setdefault("quality_metric_provenance", {})["bertscore"] = provenance

    rows_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary_path = rows_path.with_name("summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["methods"] = build._summary(rows)
    summary["methods_clustered_by_post"] = build._clustered_method_summary(rows)
    summary["row_level_descriptive_statistics"] = build._row_level_paired_stats(rows)
    summary["paired_statistics"] = build._clustered_paired_stats(rows)
    summary["bertscore_recomputed"] = {
        "input": str(rows_path),
        "model": args.bertscore_model,
        "device": args.device,
        "rescale_with_baseline": True,
        "provenance": provenance,
        "means_before": before,
        "means_after": _means(rows),
        "note": "BERTScore only; BLEU/ROUGE/perplexity/KL/JSD unchanged.",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    after = _means(rows)
    print(json.dumps({"rows": len(rows), "provenance": provenance, "before": before, "after": after}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
