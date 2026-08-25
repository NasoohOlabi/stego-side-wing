"""Score cached Codex judgments and merge pointwise fields into paired rows."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from services.judge_scoring_service import (  # noqa: E402
    auroc,
    holm_adjust,
    post_cluster_summary,
)


def _load(path: Path) -> list[dict[str, Any]]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if path.exists()
        else []
    )


def _dedupe_by_task_id(judgments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one cached result per deterministic task, preferring valid output."""
    deduped: dict[str, dict[str, Any]] = {}
    for judgment in judgments:
        task_id = str(judgment.get("task_id") or "")
        if not task_id:
            continue
        existing = deduped.get(task_id)
        if existing is None or (
            existing.get("error") is not None and judgment.get("error") is None
        ):
            deduped[task_id] = judgment
    return list(deduped.values())


def _matching_config(
    judgments: list[dict[str, Any]], backend: str | None, model: str | None, reasoning_effort: str | None
) -> list[dict[str, Any]]:
    return [
        judgment
        for judgment in judgments
        if (backend is None or judgment.get("judge_backend") == backend)
        and (model is None or judgment.get("judge_model") == model)
        and (reasoning_effort is None or judgment.get("reasoning_effort") == reasoning_effort)
    ]


def _value(judgment: dict[str, Any], key: str) -> Any:
    result = judgment.get("result")
    return result.get(key) if isinstance(result, dict) else None


def _merge(rows: list[dict[str, Any]], judgments: list[dict[str, Any]], metric: str) -> None:
    by_key = {
        (str(x.get("pair_id")), str(x.get("method"))): x
        for x in judgments
        if x.get("error") is None
    }
    for row in rows:
        judgment = by_key.get((str(row.get("pair_id")), str(row.get("method"))))
        if judgment is None:
            continue
        result = judgment.get("result") or {}
        provenance = {
            key: judgment.get(key)
            for key in (
                "judge_backend",
                "judge_model",
                "reasoning_effort",
                "codex_cli_version",
                "judge_prompt_sha256",
                "output_schema_sha256",
                "usage",
            )
        }
        row.setdefault("codex_judge_provenance", {})[metric] = provenance
        if metric == "suspicion":
            row["codex_suspicion"] = result.get("suspicion")
            row["codex_suspicion_tells"] = result.get("tells")
        if metric == "attribution":
            row["codex_attribution_correct"] = result.get("thread_index") == (
                judgment.get("answer") or {}
            ).get("thread_index")
        if metric == "register":
            for key in (
                "tone_formality",
                "length_norm",
                "mechanics",
                "insider_knowledge",
                "overall",
            ):
                row[f"codex_register_{key}"] = result.get(key)


def _summary(metric: str, judgments: list[dict[str, Any]], methods: tuple[str, str]) -> dict[str, Any]:
    valid = [x for x in judgments if x.get("error") is None and isinstance(x.get("result"), dict)]
    output: dict[str, Any] = {"valid_judgments": len(valid), "tasks": len(judgments)}
    if metric in {"suspicion", "register"}:
        key = "suspicion" if metric == "suspicion" else "overall"
        rows = [
            {"post_id": x.get("post_id"), "method": x.get("method"), key: _value(x, key)}
            for x in valid
            if x.get("method") != "human"
        ]
        output["post_cluster"] = post_cluster_summary(rows, key, *methods)
        if metric == "suspicion":
            humans = [_value(x, key) for x in valid if x.get("method") == "human"]
            output["auroc"] = {
                m: auroc([_value(x, key) for x in valid if x.get("method") == m], humans)
                for m in methods
            }
    if metric == "attribution":
        pairs = [
            (
                bool(_value(x, "thread_index") == (x.get("answer") or {}).get("thread_index")),
                x.get("method"),
            )
            for x in valid
            if x.get("method") != "human"
        ]
        output["accuracy"] = {
            m: sum(ok for ok, method in pairs if method == m)
            / max(1, sum(method == m for _, method in pairs))
            for m in methods
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metric",
        choices=("standout", "weak_link", "suspicion", "attribution", "register"),
        required=True,
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--backend")
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--control-method", default="our_method")
    parser.add_argument("--treatment-method", default="zlg")
    args = parser.parse_args()
    dataset = Path(args.run_dir) / "comparison_dataset"
    directory = dataset / "codex_judgments"
    judgments_path = directory / f"{args.metric}_judgments.jsonl"
    judgments = _dedupe_by_task_id(_load(judgments_path))
    judgments_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in judgments) + "\n",
        encoding="utf-8",
    )
    scored_judgments = _matching_config(
        judgments, args.backend, args.model, args.reasoning_effort
    )
    rows_path = dataset / "paired_rows.jsonl"
    rows = _load(rows_path)
    if args.metric in {"suspicion", "attribution", "register"}:
        backup = rows_path.with_name("paired_rows.jsonl.bak_pre_codex_judge")
        if not backup.exists():
            shutil.copy2(rows_path, backup)
        _merge(rows, scored_judgments, args.metric)
        rows_path.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in rows) + "\n",
            encoding="utf-8",
        )
    summary_path = dataset / "codex_judge_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary[args.metric] = _summary(
        args.metric, scored_judgments, (args.control_method, args.treatment_method)
    )
    primary = [
        x.get("post_cluster", {}).get("two_sided_sign_test_p")
        for x in summary.values()
        if isinstance(x, dict) and "post_cluster" in x
    ]
    summary["holm_adjusted_primary_p_values"] = holm_adjust(primary)
    summary["provenance"] = {
        "tasks_are_seeded": True,
        "reasoning_models_are_not_deterministic": True,
        "inference_unit": "post_id",
        "cluster_bootstrap_iterations": 10000,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
