#!/usr/bin/env python3
"""Recompute KL/JSD on an existing comparison_dataset after baseline-walk fixes.

CPU-only: updates ``kl_*`` / ``jsd_*`` on each paired row from ``stegotext``, then
refreshes ``summary.json`` aggregates and paired sign tests. Does not touch
perplexity, BLEU/ROUGE/BERTScore, or capacity fields.

Example::

    uv run python scripts/recompute_paired_divergence_metrics.py \\
      --zlg-run-dir metrics/zlg_comparison_runs/zlg_batch_scale300 \\
      --dataset-dir metrics/e2e_runs/scale300_chunk1/balanced/dataset
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from services.stego_metrics_service import (  # noqa: E402
    _kl_jsd_pair,
    extract_comment_counter,
    load_global_stats,
    tokenize,
)

# Import summary helpers from the build script without re-running its CLI.
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import build_zlg_method_comparison_dataset as build  # noqa: E402


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _resolve_rows_path(comparison_dir: Path) -> Path:
    primary = comparison_dir / "paired_rows.jsonl"
    if primary.is_file():
        return primary
    backup = comparison_dir / "paired_rows.jsonl.bak_pre_codex_judge"
    if backup.is_file():
        return backup
    raise FileNotFoundError(f"No paired rows under {comparison_dir}")


def _recompute_row(
    row: dict[str, Any],
    *,
    primary_by_post: dict[str, Counter],
    global_counter: Counter,
    alpha: float,
) -> None:
    stego = str(row.get("stegotext") or "")
    stego_counter = Counter(tokenize(stego))
    post_id = str(row.get("post_id") or "")
    primary = primary_by_post.get(post_id)
    kl_p, jsd_p = _kl_jsd_pair(stego_counter, primary, alpha)
    kl_g, jsd_g = _kl_jsd_pair(stego_counter, global_counter, alpha)
    row["kl_matched_post"] = kl_p
    row["jsd_matched_post"] = jsd_p
    row["kl_global_corpus"] = kl_g
    row["jsd_global_corpus"] = jsd_g


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zlg-run-dir", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--alpha", type=float, default=1e-6)
    args = parser.parse_args()

    zlg_run_dir = Path(args.zlg_run_dir).resolve()
    dataset_dir = Path(args.dataset_dir).resolve()
    comparison_dir = zlg_run_dir / "comparison_dataset"
    source_path = _resolve_rows_path(comparison_dir)
    rows = _load_jsonl(source_path)
    post_ids = {str(r["post_id"]) for r in rows if r.get("post_id")}
    primary_by_post = {
        post_id: extract_comment_counter(dataset_dir / f"{post_id}.json")
        for post_id in sorted(post_ids)
        if (dataset_dir / f"{post_id}.json").is_file()
    }
    _, global_counter, nonempty_bodies = load_global_stats(dataset_dir, None)
    for row in rows:
        _recompute_row(
            row,
            primary_by_post=primary_by_post,
            global_counter=global_counter,
            alpha=args.alpha,
        )

    out_rows = comparison_dir / "paired_rows.jsonl"
    out_rows.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    summary_path = comparison_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["methods"] = build._summary(rows)
    summary["methods_clustered_by_post"] = build._clustered_method_summary(rows)
    summary["row_level_descriptive_statistics"] = build._row_level_paired_stats(rows)
    summary["paired_statistics"] = build._clustered_paired_stats(rows)
    summary["divergence_recomputed"] = {
        "source_rows": str(source_path),
        "dataset_dir": str(dataset_dir),
        "alpha": args.alpha,
        "global_nonempty_comment_bodies": nonempty_bodies,
        "posts_with_primary_baseline": len(primary_by_post),
        "note": (
            "KL/JSD recomputed with nested-reply comment walk matching "
            "_walk_comment_bodies; perplexity and reference metrics unchanged."
        ),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    our = summary["methods"]["our_method"]
    zlg = summary["methods"]["zlg"]
    print(f"Wrote {out_rows} ({len(rows)} rows)")
    print(f"Global nonempty comment bodies: {nonempty_bodies}")
    for key in (
        "kl_matched_post_mean",
        "jsd_matched_post_mean",
        "kl_global_corpus_mean",
        "jsd_global_corpus_mean",
    ):
        print(f"  ours {key}={our.get(key):.6f}  zlg={zlg.get(key):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
