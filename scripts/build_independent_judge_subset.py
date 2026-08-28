"""Build a one-pair-per-post judge subset with unique ZLG cover texts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def select_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select the first pair per post whose ZLG text occurs only once."""
    pairs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        pairs[str(row.get("pair_id"))][str(row.get("method"))] = row
    complete = {
        pair_id: pair
        for pair_id, pair in pairs.items()
        if {"our_method", "zlg"}.issubset(pair)
    }
    zlg_counts = Counter(str(pair["zlg"].get("stegotext", "")) for pair in complete.values())
    selected: list[str] = []
    used_posts: set[str] = set()
    for pair_id, pair in sorted(complete.items()):
        post_id = str(pair["our_method"].get("post_id"))
        zlg_text = str(pair["zlg"].get("stegotext", ""))
        if post_id not in used_posts and zlg_counts[zlg_text] == 1:
            selected.append(pair_id)
            used_posts.add(post_id)
    selected_set = set(selected)
    output = [row for row in rows if str(row.get("pair_id")) in selected_set]
    return output, {
        "source_pairs": len(complete),
        "source_posts": len({str(pair["our_method"].get("post_id")) for pair in complete.values()}),
        "selected_pairs": len(selected),
        "selected_posts": len(used_posts),
    }


def dataset_sources(rows: list[dict[str, Any]]) -> dict[str, Path]:
    """Find the campaign dataset file associated with each selected post."""
    sources: dict[str, Path] = {}
    for row in rows:
        post_id = str(row["post_id"])
        source_output = Path(str(row["source_output_file"]))
        sources.setdefault(post_id, source_output.parent.parent / "dataset" / f"{post_id}.json")
    return sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dataset-output", type=Path)
    args = parser.parse_args()
    source = args.input.read_bytes()
    rows = [json.loads(line) for line in source.decode("utf-8").splitlines() if line.strip()]
    selected, counts = select_rows(rows)
    if args.dataset_output is not None:
        args.dataset_output.mkdir(parents=True, exist_ok=True)
        sources = dataset_sources(selected)
        for post_id, source_path in sources.items():
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            shutil.copyfile(source_path, args.dataset_output / f"{post_id}.json")
        counts["dataset_posts"] = len(sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in selected) + "\n",
        encoding="utf-8",
    )
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(
            {
                "selection": "first_pair_per_post_with_globally_unique_zlg_stegotext",
                "source_sha256": hashlib.sha256(source).hexdigest(),
                **counts,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
