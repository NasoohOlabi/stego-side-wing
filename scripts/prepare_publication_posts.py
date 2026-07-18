"""Prepare new real posts and model-generated angles before freezing a benchmark manifest."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from workflows.pipelines.gen_angles import GenAnglesPipeline  # noqa: E402
from workflows.pipelines.research import ResearchPipeline  # noqa: E402
from workflows.utils.stego_codec import flatten_nested_angles  # noqa: E402

LOG = logger.bind(component="PublicationPostPreparation")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _used_zlg_post_ids(root: Path) -> set[str]:
    used: set[str] = set()
    for path in root.glob("**/results.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("post_id"):
                used.add(str(row["post_id"]))
    return used


def _existing_ids(output_dir: Path) -> set[str]:
    return {path.stem for path in output_dir.glob("*.json")}


def select_candidates(dataset_dir: Path, excluded: set[str]) -> list[Path]:
    return [path for path in sorted(dataset_dir.glob("*.json")) if path.stem not in excluded]


def _prepare(post: dict[str, Any]) -> dict[str, Any]:
    researched = ResearchPipeline().preview_post(post, force=True)["post"]
    angled = GenAnglesPipeline().preview_post(researched, allow_fallback=False)["post"]
    if len(flatten_nested_angles(angled)) < 2:
        raise RuntimeError("Prepared post has fewer than two tangents")
    return angled


def _reuse_prepared_angle(source: Path, destination: Path) -> bool:
    angled = _read_json(source)
    if len(flatten_nested_angles(angled)) < 2:
        return False
    shutil.copyfile(source, destination)
    return True


def _write_ids(path: Path, post_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(post_ids) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--dataset-dir", default="datasets/news_cleaned")
    parser.add_argument("--reuse-angles-dir", default="datasets/news_angles")
    parser.add_argument("--output-dir", default="metrics/benchmark/prepared_angles")
    parser.add_argument("--post-ids-output", default="metrics/benchmark/post_ids.txt")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    dataset_dir = (ROOT / args.dataset_dir).resolve()
    reuse_dir = (ROOT / args.reuse_angles_dir).resolve()
    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    excluded = _used_zlg_post_ids(ROOT / "metrics" / "zlg_comparison_runs")
    prepared = sorted(_existing_ids(output_dir) - excluded)
    for path in select_candidates(dataset_dir, excluded | set(prepared)):
        if len(prepared) >= args.count:
            break
        trace = uuid4().hex
        try:
            reused = reuse_dir / path.name
            if reused.exists() and _reuse_prepared_angle(reused, output_dir / path.name):
                LOG.bind(trace_id=trace, post_id=path.stem).info("benchmark_post_reused")
            elif args.allow_live:
                angled = _prepare(_read_json(path))
                (output_dir / path.name).write_text(
                    json.dumps(angled, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            else:
                continue
            prepared.append(path.stem)
            _write_ids((ROOT / args.post_ids_output).resolve(), prepared)
            LOG.bind(trace_id=trace, post_id=path.stem).info("benchmark_post_prepared")
        except Exception:
            LOG.bind(trace_id=trace, post_id=path.stem).exception("benchmark_post_prepare_failed")
    return 0 if len(prepared) >= args.count else 2


if __name__ == "__main__":
    raise SystemExit(main())
