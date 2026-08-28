"""Build deterministic Codex judge tasks and append resumable judgment rows."""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from services.codex_judge_client import (
    CodexJudgeConfig,
    default_model_for_backend,
    run_codex_judge,
)
from services.judge_slate_service import (
    build_attribution,
    build_pointwise,
    build_register,
    build_standout,
    build_weak_link,
    load_post_comments,
)

METRICS = ("standout", "weak_link", "suspicion", "attribution", "register")


def _load(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _render(template: str, fields: dict[str, Any]) -> str:
    for key, value in fields.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _pairs(
    rows: list[dict[str, Any]], control_method: str, treatment_method: str
) -> list[dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("pair_id")), {})[str(row.get("method"))] = row
    return [x for _, x in sorted(grouped.items()) if {control_method, treatment_method}.issubset(x)]


def _tasks(
    metric: str,
    rows: list[dict[str, Any]],
    dataset: Path,
    limit: int | None,
    control_method: str,
    treatment_method: str,
) -> list[dict[str, Any]]:
    corpus = []
    cache: dict[str, tuple[dict[str, Any], list[str]]] = {}
    pairs = _pairs(rows, control_method, treatment_method)
    if limit is not None:
        pairs = pairs[:limit]
    for pair in pairs:
        post_id = str(pair[control_method]["post_id"])
        post, comments = load_post_comments(dataset, post_id)
        cache[post_id] = (post, comments)
        corpus.append(post)
    result = []
    for pair in pairs:
        internal_pair = {"our_method": pair[control_method], "zlg": pair[treatment_method]}
        post, comments = cache[str(internal_pair["our_method"]["post_id"])]
        if metric == "standout" and len(comments) >= 9:
            result.extend(x.model_dump() for x in build_standout(internal_pair, post, comments))
        elif metric == "weak_link" and comments:
            result.append(build_weak_link(internal_pair, post, comments).model_dump())
        elif metric == "suspicion" and comments:
            result.extend(x.model_dump() for x in build_pointwise(metric, internal_pair, post, comments))
        elif metric == "register" and comments:
            result.extend(x.model_dump() for x in build_register(internal_pair, post, comments))
        elif metric == "attribution" and len(corpus) >= 4 and comments:
            result.extend(x.model_dump() for x in build_attribution(internal_pair, post, comments, corpus))
    for task in result:
        if task["method"] == "our_method":
            task["method"] = control_method
        elif task["method"] == "zlg":
            task["method"] = treatment_method
    return result


def _run(
    task: dict[str, Any],
    template: str,
    schema: Path,
    prompt_hash: str,
    schema_hash: str,
    config: CodexJudgeConfig,
) -> dict[str, Any]:
    task_id = _task_id(task, prompt_hash, schema_hash, config)
    result = run_codex_judge(_render(template, task["prompt_fields"]), schema, config)
    return {
        **task,
        "task_id": task_id,
        "result": result.parsed,
        "raw_response": result.text,
        "error": result.error,
        "error_detail": result.error_detail,
        "judge_backend": config.backend,
        "judge_model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "codex_cli_version": result.codex_version,
        "judge_prompt_sha256": prompt_hash,
        "output_schema_sha256": schema_hash,
        "usage": result.usage,
        "attempts": result.attempts,
    }


def _task_id(
    task: dict[str, Any], prompt_hash: str, schema_hash: str, config: CodexJudgeConfig
) -> str:
    identity = (
        f"{task['metric']}:{task['pair_id']}:{task['method']}:{prompt_hash}:"
        f"{schema_hash}:{config.backend}:{config.model}:{config.reasoning_effort}"
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def _write_progress(path: Path, metric: str, total: int, completed: int, errors: int) -> None:
    status = {
        "metric": metric,
        "total_tasks": total,
        "completed": completed,
        "pending": total - completed,
        "errors": errors,
        "complete": completed == total,
    }
    path.write_text(json.dumps(status, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metric", choices=METRICS, required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--dataset-dir", default=str(ROOT / "metrics/e2e_runs/scale300_combined_dataset")
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--backend", choices=("claude", "codex"), default="claude")
    parser.add_argument(
        "--model",
        default=None,
        help="Judge model; defaults to haiku (claude) or gpt-5.6-luna (codex).",
    )
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--control-method", default="our_method")
    parser.add_argument("--treatment-method", default="zlg")
    args = parser.parse_args()
    directory = Path(args.run_dir) / "comparison_dataset" / "codex_judgments"
    directory.mkdir(parents=True, exist_ok=True)
    template_path = ROOT / "config/evaluation_prompts" / f"{args.metric}_v1.txt"
    schema = ROOT / "config/evaluation_prompts/schemas" / f"{args.metric}_v1.schema.json"
    template = template_path.read_text(encoding="utf-8")
    prompt_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()
    schema_hash = hashlib.sha256(schema.read_bytes()).hexdigest()
    tasks = _tasks(
        args.metric,
        _load(Path(args.run_dir) / "comparison_dataset/paired_rows.jsonl"),
        Path(args.dataset_dir),
        args.limit, args.control_method, args.treatment_method,
    )
    model = args.model or default_model_for_backend(args.backend)
    config = CodexJudgeConfig(
        backend=args.backend,
        model=model,
        reasoning_effort=args.reasoning_effort,
        ignore_user_config=args.backend == "codex",
    )
    for task in tasks:
        task["task_id"] = _task_id(task, prompt_hash, schema_hash, config)
    public_tasks = [
        {key: value for key, value in task.items() if key != "answer"} for task in tasks
    ]
    (directory / f"{args.metric}_tasks.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in public_tasks) + "\n", encoding="utf-8"
    )
    (directory / f"{args.metric}_answer_key.jsonl").write_text(
        "\n".join(json.dumps({"task_id": x["task_id"], "answer": x["answer"]}) for x in tasks)
        + "\n",
        encoding="utf-8",
    )
    output = directory / f"{args.metric}_judgments.jsonl"
    task_ids = {task["task_id"] for task in tasks}
    done = (
        {
            row.get("task_id")
            for row in _load(output)
            if row.get("task_id") in task_ids and row.get("error") is None
        }
        if output.exists()
        else set()
    )
    pending = [task for task in tasks if task["task_id"] not in done]
    progress = directory / f"{args.metric}_progress.json"
    completed, errors = len(done), 0
    _write_progress(progress, args.metric, len(tasks), completed, errors)
    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = [
            pool.submit(_run, task, template, schema, prompt_hash, schema_hash, config)
            for task in pending
        ]
        with output.open("a", encoding="utf-8") as stream:
            for future in as_completed(futures):
                row = future.result()
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                completed += 1
                errors += int(row["error"] is not None)
                _write_progress(progress, args.metric, len(tasks), completed, errors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
