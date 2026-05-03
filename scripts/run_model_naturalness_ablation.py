"""Run mixed-provider model ablations for stego naturalness."""

import argparse
import json
import os
import shutil
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import requests
from loguru import logger
from pydantic import BaseModel, Field, validate_call

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from run_actual_workload_e2e import (  # noqa: E402
    DEFAULT_MAX_TRANSIENT_SAMPLE_RETRIES,
    DEFAULT_TRANSIENT_SAMPLE_RETRY_BASE_DELAY_SECONDS,
    _run_profile,
    _select_post_ids,
)

from infrastructure.config import (  # noqa: E402
    get_google_generative_language_api_key,
    get_lm_studio_url,
)
from infrastructure.json_logging import configure_api_logging  # noqa: E402
from services.stego_experiment_service import resolve_experiment_variants  # noqa: E402
from services.stego_metrics_service import extract_stego_text_unified  # noqa: E402
from workflows.utils.protocol_utils import text_preview  # noqa: E402
from workflows.utils.stego_codec import strip_invisible_payload  # noqa: E402

RUNS_ROOT = _REPO_ROOT / "metrics" / "model_ablation_runs"
DEFAULT_LM_STUDIO_MODELS = ("openai/gpt-oss-20b", "qwen/qwen3.5-9b")
MODEL_ENV_KEYS = (
    "WORKFLOW_LLM_BACKEND",
    "WORKFLOW_LM_STUDIO_MODEL",
    "GOOGLE_AI_STUDIO_MODEL",
    "ANGLES_MODEL",
    "MODEL",
)
_LOG = logger.bind(component="ModelNaturalnessAblation")


class ModelLane(BaseModel):
    """One provider/model lane, blinded by lane_id in judge exports."""

    lane_id: str = Field(min_length=1)
    provider: Literal["lm_studio", "google"]
    model: str = Field(min_length=1)
    skip_reason: str | None = None


class PreflightResult(BaseModel):
    """Available and skipped lanes after provider model checks."""

    available_lanes: list[ModelLane]
    skipped_lanes: list[ModelLane]
    lm_studio_models: list[str] = Field(default_factory=list)
    google_models: list[str] = Field(default_factory=list)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_lane_slug(lane: ModelLane) -> str:
    safe_model = "".join(ch if ch.isalnum() else "_" for ch in lane.model.lower()).strip("_")
    return f"{lane.lane_id}_{lane.provider}_{safe_model}"[:96]


def _assert_overwrite_target(path: Path) -> None:
    path.resolve().relative_to(_REPO_ROOT.resolve())


def _prepare_run_dir(run_dir: Path | None, *, overwrite: bool) -> Path:
    created = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    resolved = (run_dir or RUNS_ROOT / f"model_naturalness_{created}").resolve()
    if resolved.exists():
        if not overwrite:
            raise FileExistsError(f"Run directory exists: {resolved}")
        _assert_overwrite_target(resolved)
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _auth_headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "ngrok" in get_lm_studio_url().lower():
        headers["ngrok-skip-browser-warning"] = "true"
    return headers


def list_lm_studio_model_ids(timeout_sec: float = 20.0) -> list[str]:
    """OpenAI-compatible model IDs from LM Studio `/models`."""
    url = f"{get_lm_studio_url().rstrip('/')}/models"
    token = os.environ.get("LM_STUDIO_API_TOKEN", "lm-studio")
    response = requests.get(url, headers=_auth_headers(token), timeout=timeout_sec)
    response.raise_for_status()
    data = response.json()
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return sorted(
        {
            str(item.get("id")).strip()
            for item in items
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
    )


def _google_model_id(raw_name: Any) -> str:
    name = str(raw_name or "").strip()
    return name.removeprefix("models/")


def list_google_model_ids(timeout_sec: float = 20.0) -> list[str]:
    """Generative Language API model IDs from Google `models.list`."""
    api_key = get_google_generative_language_api_key()
    if not api_key:
        return []
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    models: list[str] = []
    params: dict[str, str] = {"key": api_key}
    while True:
        response = requests.get(url, params=params, timeout=timeout_sec)
        response.raise_for_status()
        payload = response.json()
        raw_models = payload.get("models") if isinstance(payload, dict) else None
        if isinstance(raw_models, list):
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                methods = item.get("supportedGenerationMethods")
                if isinstance(methods, list) and "generateContent" not in methods:
                    continue
                model_id = _google_model_id(item.get("name"))
                if model_id:
                    models.append(model_id)
        token = payload.get("nextPageToken") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            break
        params["pageToken"] = token
    return sorted(set(models))


def discover_google_gemma_models(*, max_models: int) -> list[str]:
    model_ids = list_google_model_ids()
    gemmas = [model_id for model_id in model_ids if "gemma" in model_id.lower()]
    return gemmas[: max(0, max_models)]


def _assign_lane_ids(lanes: Sequence[tuple[str, str]]) -> list[ModelLane]:
    return [
        ModelLane(
            lane_id=f"lane_{idx:03d}",
            provider=provider,  # type: ignore[arg-type]
            model=model,
        )
        for idx, (provider, model) in enumerate(lanes, start=1)
    ]


@validate_call
def preflight_model_lanes(
    *,
    lm_studio_models: list[str] | tuple[str, ...] = DEFAULT_LM_STUDIO_MODELS,
    google_models: list[str] | tuple[str, ...] = (),
    max_gemma_models: int = 4,
    skip_preflight: bool = False,
) -> PreflightResult:
    """Resolve requested model lanes and skip unavailable providers/models."""
    requested_google = list(google_models) or (
        [] if skip_preflight else discover_google_gemma_models(max_models=max_gemma_models)
    )
    requested = [(("lm_studio"), model) for model in lm_studio_models]
    requested.extend(("google", model) for model in requested_google)
    candidate_lanes = _assign_lane_ids(requested)
    if skip_preflight:
        return PreflightResult(available_lanes=candidate_lanes, skipped_lanes=[])

    available: list[ModelLane] = []
    skipped: list[ModelLane] = []
    try:
        lm_available = set(list_lm_studio_model_ids())
    except Exception as exc:
        lm_available = set()
        lm_error = f"LM Studio /models failed: {type(exc).__name__}: {exc}"
    else:
        lm_error = ""
    try:
        google_available_list = list_google_model_ids()
        google_available = set(google_available_list)
    except Exception as exc:
        google_available_list = []
        google_available = set()
        google_error = f"Google models.list failed: {type(exc).__name__}: {exc}"
    else:
        google_error = ""

    for lane in candidate_lanes:
        if lane.provider == "lm_studio":
            if lane.model in lm_available:
                available.append(lane)
            else:
                skipped.append(lane.model_copy(update={"skip_reason": lm_error or "model absent"}))
            continue
        if lane.model in google_available:
            available.append(lane)
        else:
            skipped.append(lane.model_copy(update={"skip_reason": google_error or "model absent"}))
    return PreflightResult(
        available_lanes=available,
        skipped_lanes=skipped,
        lm_studio_models=sorted(lm_available),
        google_models=google_available_list,
    )


@contextmanager
def applied_model_lane(lane: ModelLane) -> Iterator[None]:
    """Temporarily route workflow LLM calls through one model lane."""
    old_values = {key: os.environ.get(key) for key in MODEL_ENV_KEYS}
    try:
        if lane.provider == "lm_studio":
            os.environ["WORKFLOW_LLM_BACKEND"] = "lm_studio"
            os.environ["WORKFLOW_LM_STUDIO_MODEL"] = lane.model
            os.environ["ANGLES_MODEL"] = lane.model
            os.environ["MODEL"] = lane.model
            os.environ.pop("GOOGLE_AI_STUDIO_MODEL", None)
        else:
            os.environ["WORKFLOW_LLM_BACKEND"] = "google"
            os.environ["GOOGLE_AI_STUDIO_MODEL"] = lane.model
            os.environ.pop("WORKFLOW_LM_STUDIO_MODEL", None)
            os.environ.pop("ANGLES_MODEL", None)
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _metric_number(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rating_value(payload: dict[str, Any], *keys: str) -> float | None:
    rating = payload.get("rating")
    merged = dict(rating) if isinstance(rating, dict) else {}
    merged.update(payload)
    for key in keys:
        value = merged.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def load_judge_rating_summary(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    grouped: dict[str, dict[str, list[float]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            continue
        lane_id = payload.get("lane_id")
        if not isinstance(lane_id, str):
            continue
        naturalness = _rating_value(payload, "naturalness_1_to_5", "naturalness")
        context_fit = _rating_value(payload, "context_fit_1_to_5", "context_fit")
        bucket = grouped.setdefault(lane_id, {"naturalness": [], "context_fit": []})
        if naturalness is not None:
            bucket["naturalness"].append(naturalness)
        if context_fit is not None:
            bucket["context_fit"].append(context_fit)
    return {
        lane_id: {
            "judge_naturalness_mean": _mean(values["naturalness"]),
            "judge_context_fit_mean": _mean(values["context_fit"]),
            "judge_rated_samples": len(values["naturalness"]),
        }
        for lane_id, values in grouped.items()
    }


def _source_context(dataset_file: Path) -> dict[str, Any]:
    try:
        data = _read_json(dataset_file)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    comments = data.get("comments")
    previews: list[str] = []
    if isinstance(comments, list):
        for comment in comments[:5]:
            if isinstance(comment, dict) and isinstance(comment.get("body"), str):
                previews.append(text_preview(str(comment["body"]), limit=240))
    return {
        "title": data.get("title") if isinstance(data.get("title"), str) else "",
        "selftext_preview": text_preview(str(data.get("selftext") or ""), limit=320),
        "comment_previews": previews,
    }


def _judge_rows_for_lane(lane: ModelLane, lane_summary: dict[str, Any]) -> list[dict[str, Any]]:
    entries = lane_summary.get("entries")
    if not isinstance(entries, list):
        return []
    metrics_report = lane_summary.get("metrics_report")
    if not isinstance(metrics_report, dict):
        metrics_report = {}
    config = metrics_report.get("config")
    if not isinstance(config, dict):
        config = {}
    dataset_dir = Path(str(config.get("dataset_dir", "")))
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        output_file_raw = entry.get("output_file")
        if not isinstance(output_file_raw, str):
            continue
        output_file = Path(output_file_raw)
        try:
            output_payload = _read_json(output_file)
        except Exception:
            continue
        stego_text = extract_stego_text_unified(output_payload)
        if not isinstance(stego_text, str) or not stego_text.strip():
            continue
        sample_index = entry.get("sample_index")
        post_id = str(entry.get("post_id") or output_file.stem.split("_version_")[0])
        sample_id = f"{lane.lane_id}:{post_id}:{sample_index}"
        rows.append(
            {
                "lane_id": lane.lane_id,
                "sample_id": sample_id,
                "post_id": post_id,
                "visible_stego_text": strip_invisible_payload(stego_text),
                "source_context": _source_context(dataset_dir / f"{post_id}.json"),
                "rating_template": {
                    "naturalness_1_to_5": None,
                    "context_fit_1_to_5": None,
                    "artifact_notes": "",
                },
            }
        )
    return rows


def _write_judge_instructions(path: Path) -> None:
    text = (
        "# Blind Naturalness Ratings\n\n"
        "Rate each JSONL row without using any model identity. Keep `lane_id` and `sample_id` "
        "unchanged, then add numeric ratings:\n\n"
        "- `naturalness_1_to_5`: 5 = reads like an ordinary human comment.\n"
        "- `context_fit_1_to_5`: 5 = fits the provided post/comment context.\n"
        "- `artifact_notes`: short note for odd phrasing, obvious prompt artifacts, or incoherence.\n"
    )
    path.write_text(text, encoding="utf-8")


def _lane_summary_row(
    lane: ModelLane,
    summary: dict[str, Any],
    judge_summary: dict[str, Any],
) -> dict[str, Any]:
    metrics = summary.get("summary_metrics")
    metrics_dict = metrics if isinstance(metrics, dict) else {}
    quality = metrics_dict.get("quality_metrics")
    quality_dict = quality if isinstance(quality, dict) else {}
    entries = summary.get("entries")
    entry_list = entries if isinstance(entries, list) else []
    retries = [
        float(entry["retry_count"])
        for entry in entry_list
        if isinstance(entry, dict) and isinstance(entry.get("retry_count"), (int, float))
    ]
    requested = int(summary.get("requested_samples") or len(entry_list) or 0)
    failed = int(summary.get("samples_failed") or 0)
    row = {
        "lane_id": lane.lane_id,
        "provider": lane.provider,
        "model": lane.model,
        "samples_succeeded": int(summary.get("samples_succeeded") or 0),
        "samples_failed": failed,
        "failure_rate": failed / requested if requested > 0 else None,
        "average_retry_count": _mean(retries),
        "receiver_success_rate": quality_dict.get("receiver_success_rate"),
        "matched_post_kl": quality_dict.get("matched_post_kl"),
        "matched_post_jsd": quality_dict.get("matched_post_jsd"),
        "perplexity": quality_dict.get("perplexity"),
        "run_dir": summary.get("run_dir"),
    }
    row.update(judge_summary)
    return row


def _sort_leaderboard(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
        judge = _metric_number(row, "judge_naturalness_mean")
        return (
            0.0 if judge is not None else 1.0,
            -(judge or 0.0),
            _metric_number(row, "matched_post_jsd") or float("inf"),
            _metric_number(row, "matched_post_kl") or float("inf"),
            _metric_number(row, "perplexity") or float("inf"),
            _metric_number(row, "failure_rate") or float("inf"),
        )

    ranked = sorted(rows, key=key)
    return [dict(row, rank=idx) for idx, row in enumerate(ranked, start=1)]


@validate_call(config={"arbitrary_types_allowed": True})
def run_model_naturalness_ablation(
    *,
    samples_per_model: int,
    post_ids: list[str] | tuple[str, ...],
    angles_dir: Path,
    dataset_dir: Path,
    run_dir: Path | None = None,
    overwrite: bool = False,
    max_retries: int = 1,
    allow_post_reuse: bool = False,
    skip_receiver_decode: bool = False,
    fail_fast: bool = False,
    lm_studio_models: list[str] | tuple[str, ...] = DEFAULT_LM_STUDIO_MODELS,
    google_models: list[str] | tuple[str, ...] = (),
    max_gemma_models: int = 4,
    skip_preflight: bool = False,
    judge_ratings_path: Path | None = None,
    max_transient_sample_retries: int = DEFAULT_MAX_TRANSIENT_SAMPLE_RETRIES,
    transient_sample_retry_base_delay_seconds: float = (
        DEFAULT_TRANSIENT_SAMPLE_RETRY_BASE_DELAY_SECONDS
    ),
) -> dict[str, Any]:
    if samples_per_model <= 0:
        raise ValueError("samples_per_model must be positive")
    resolved_run_dir = _prepare_run_dir(run_dir, overwrite=overwrite)
    preflight = preflight_model_lanes(
        lm_studio_models=lm_studio_models,
        google_models=google_models,
        max_gemma_models=max_gemma_models,
        skip_preflight=skip_preflight,
    )
    selected_post_ids = _select_post_ids(
        explicit_post_ids=post_ids,
        angles_dir=angles_dir,
        dataset_dir=dataset_dir,
        samples_per_profile=samples_per_model,
        allow_post_reuse=allow_post_reuse,
    )[:samples_per_model]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    balanced_variant = resolve_experiment_variants(["balanced"])[0]
    lane_summaries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    judge_rows: list[dict[str, Any]] = []

    for lane in preflight.available_lanes:
        lane_dir = resolved_run_dir / _safe_lane_slug(lane)
        try:
            with applied_model_lane(lane):
                lane_summary = _run_profile(
                    run_id=run_id,
                    variant=balanced_variant,
                    post_ids=selected_post_ids,
                    run_dir=lane_dir,
                    angles_dir=angles_dir,
                    dataset_dir=dataset_dir,
                    max_retries=max_retries,
                    force_model_generation=True,
                    skip_receiver_decode=skip_receiver_decode,
                    fail_fast=fail_fast,
                    max_transient_sample_retries=max_transient_sample_retries,
                    transient_sample_retry_base_delay_seconds=(
                        transient_sample_retry_base_delay_seconds
                    ),
                )
            lane_summaries.append({"lane": lane.model_dump(), "summary": lane_summary})
            judge_rows.extend(_judge_rows_for_lane(lane, lane_summary))
        except Exception as exc:
            failure = {
                "lane": lane.model_dump(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            failures.append(failure)
            _write_json(resolved_run_dir / _safe_lane_slug(lane) / "failure.json", failure)
            _LOG.exception("model_lane_failed", lane_id=lane.lane_id, model=lane.model)
            if fail_fast:
                raise

    judge_samples_path = resolved_run_dir / "judge_samples.jsonl"
    _write_jsonl(judge_samples_path, judge_rows)
    _write_judge_instructions(resolved_run_dir / "judge_instructions.md")
    judge_summary_by_lane = load_judge_rating_summary(judge_ratings_path)
    leaderboard_rows = [
        _lane_summary_row(
            ModelLane.model_validate(item["lane"]),
            item["summary"],
            judge_summary_by_lane.get(str(item["lane"]["lane_id"]), {}),
        )
        for item in lane_summaries
        if isinstance(item.get("lane"), dict) and isinstance(item.get("summary"), dict)
    ]
    leaderboard = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "rows": _sort_leaderboard(leaderboard_rows),
    }
    summary = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "run_dir": str(resolved_run_dir),
        "variant": "balanced",
        "samples_per_model": samples_per_model,
        "selected_post_ids": selected_post_ids,
        "preflight": preflight.model_dump(),
        "lane_summaries": lane_summaries,
        "failures": failures,
        "judge_samples_path": str(judge_samples_path),
        "judge_ratings_path": str(judge_ratings_path) if judge_ratings_path else None,
        "leaderboard": leaderboard,
    }
    _write_json(resolved_run_dir / "leaderboard.json", leaderboard)
    _write_json(resolved_run_dir / "summary.json", summary)
    return summary


def _parse_csv_or_repeated(values: Sequence[str]) -> list[str]:
    parsed: list[str] = []
    for value in values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run mixed-provider model naturalness ablation on real prepared posts."
    )
    parser.add_argument("--samples-per-model", type=int, default=5)
    parser.add_argument("--post-id", action="append", default=[])
    parser.add_argument("--angles-dir", default=str(_REPO_ROOT / "datasets" / "news_angles"))
    parser.add_argument("--dataset-dir", default=str(_REPO_ROOT / "datasets" / "news_cleaned"))
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--allow-post-reuse", action="store_true")
    parser.add_argument("--skip-receiver-decode", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--lm-studio-model", action="append", default=[])
    parser.add_argument("--google-model", action="append", default=[])
    parser.add_argument("--max-gemma-models", type=int, default=4)
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--judge-ratings", default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    lm_models = _parse_csv_or_repeated(args.lm_studio_model) or list(DEFAULT_LM_STUDIO_MODELS)
    google_models = _parse_csv_or_repeated(args.google_model)
    run_model_naturalness_ablation(
        samples_per_model=max(1, args.samples_per_model),
        post_ids=args.post_id,
        angles_dir=Path(args.angles_dir),
        dataset_dir=Path(args.dataset_dir),
        run_dir=Path(args.run_dir).resolve() if args.run_dir else None,
        overwrite=bool(args.overwrite),
        max_retries=max(0, args.max_retries),
        allow_post_reuse=bool(args.allow_post_reuse),
        skip_receiver_decode=bool(args.skip_receiver_decode),
        fail_fast=bool(args.fail_fast),
        lm_studio_models=lm_models,
        google_models=google_models,
        max_gemma_models=max(0, args.max_gemma_models),
        skip_preflight=bool(args.skip_preflight),
        judge_ratings_path=Path(args.judge_ratings).resolve() if args.judge_ratings else None,
    )


if __name__ == "__main__":
    main()
