import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.json_logging import configure_api_logging  # noqa: E402
from infrastructure.process_tracking import append_current_pid_to_log  # noqa: E402
from loguru import logger  # noqa: E402
from services.stego_metrics_service import run_divergence_metrics  # noqa: E402
from workflows.adapters.backend_api import BackendAPIAdapter  # noqa: E402
from workflows.pipelines.data_load import DataLoadPipeline  # noqa: E402
from workflows.pipelines.gen_angles import GenAnglesPipeline  # noqa: E402
from workflows.pipelines.research import ResearchPipeline  # noqa: E402
from workflows.pipelines.stego import StegoPipeline  # noqa: E402
from workflows.utils.output_results_shape import n8n_save_object_body  # noqa: E402
from workflows.utils.stego_codec import (  # noqa: E402
    extract_invisible_payload,
    strip_invisible_payload,
)


RUNS_ROOT = _REPO_ROOT / "metrics" / "e2e_runs"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tagged_name(post_id: str, tag: str) -> str:
    return f"{post_id}_{tag}.json"


def _resolve_post_ids(
    backend: BackendAPIAdapter,
    explicit_post_ids: Sequence[str],
    *,
    count: int,
    offset: int,
) -> list[str]:
    cleaned = [post_id.strip() for post_id in explicit_post_ids if post_id.strip()]
    if cleaned:
        return [Path(post_id).stem for post_id in cleaned]
    posts_list = backend.posts_list(step="filter-url-unresolved", count=count, offset=offset)
    file_names = posts_list.get("fileNames", [])
    return [Path(file_name).stem for file_name in file_names]


def _resolve_payload(stego: StegoPipeline, payload: str | None) -> str:
    if isinstance(payload, str) and payload:
        return payload
    workflow_payload, _ = stego._load_default_payload_and_tag()
    if workflow_payload:
        return workflow_payload
    raise ValueError("Payload is required; pass --payload or configure the workflow default.")


def _selected_angle_summary(result: dict[str, Any]) -> dict[str, Any] | None:
    angle = result.get("selected_angle")
    if not isinstance(angle, dict):
        return None
    return {
        "idx": angle.get("idx"),
        "category": angle.get("category"),
        "tangent": angle.get("tangent"),
        "source_quote": angle.get("source_quote"),
        "source_document": angle.get("source_document"),
    }


def _inspection_markdown(
    *,
    tag: str,
    payload: str,
    run_dir: Path,
    metrics_report_path: str,
    metrics_report: dict[str, Any],
    entries: list[dict[str, Any]],
) -> str:
    lines: list[str] = [
        f"# Tagged E2E Run: {tag}",
        "",
        f"- Created UTC: {datetime.now(UTC).isoformat()}",
        f"- Run directory: `{run_dir}`",
        f"- Payload bytes: `{len(payload.encode('utf-8'))}`",
        f"- Payload preview: `{payload[:120]}`",
        f"- Metrics report: `{metrics_report_path}`",
        "",
        "## KLD Summary",
        "",
        f"- Primary KL: `{metrics_report['primary_baseline_matched_post']['average_kl_stego_vs_matched_post']}`",
        f"- Secondary KL: `{metrics_report['secondary_baseline_global_corpus']['average_kl_stego_vs_global_corpus']}`",
        "",
    ]
    for entry in entries:
        lines.extend(
            [
                f"## Post `{entry['post_id']}`",
                "",
                f"- Title: {entry.get('title', '')}",
                f"- URL: {entry.get('url', '')}",
                f"- Succeeded: `{entry.get('succeeded')}`",
                f"- Hidden payload bytes recovered from stego text: `{entry.get('hidden_payload_bytes')}`",
                "",
                "### Selected Angle",
                "",
                "```json",
                json.dumps(entry.get("selected_angle"), ensure_ascii=False, indent=2),
                "```",
                "",
                "### Visible Stego Text",
                "",
                "```text",
                entry.get("visible_stego_text", ""),
                "```",
                "",
                "### Original Comments",
                "",
                "```text",
                "\n\n".join(entry.get("comment_bodies", [])),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def run_tagged_e2e(
    *,
    tag: str,
    payload: str | None,
    post_ids: Sequence[str],
    count: int,
    offset: int,
    run_dir: Path | None,
    overwrite: bool,
    use_data_load_cache: bool,
    use_terms_cache: bool,
    persist_terms_cache: bool,
    use_research_fetch_cache: bool,
    allow_angles_fallback: bool,
    disable_bing_fallback: bool,
    mirror_tagged_artifacts: bool,
) -> dict[str, Any]:
    backend = BackendAPIAdapter()
    data_load = DataLoadPipeline()
    research = ResearchPipeline()
    gen_angles = GenAnglesPipeline()
    stego = StegoPipeline()

    resolved_payload = _resolve_payload(stego, payload)
    resolved_post_ids = _resolve_post_ids(backend, post_ids, count=count, offset=offset)
    if not resolved_post_ids:
        raise ValueError("No posts selected for the run.")

    resolved_run_dir = (run_dir or (RUNS_ROOT / tag)).resolve()
    if resolved_run_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"Run directory already exists: {resolved_run_dir}. Use --overwrite to replace it."
            )
        shutil.rmtree(resolved_run_dir)
    dataset_dir = resolved_run_dir / "dataset"
    data_load_dir = resolved_run_dir / "data-load"
    research_dir = resolved_run_dir / "research"
    angles_dir = resolved_run_dir / "angles"
    output_dir = resolved_run_dir / "output-results"
    metrics_dir = resolved_run_dir / "metrics"
    for path in (dataset_dir, data_load_dir, research_dir, angles_dir, output_dir, metrics_dir):
        path.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for post_id in resolved_post_ids:
        source_post = backend.get_post_local(f"{post_id}.json", "filter-url-unresolved")
        _write_json(dataset_dir / f"{post_id}.json", source_post)

        try:
            data_preview = data_load.preview_post(dict(source_post), use_cache=use_data_load_cache)
            _write_json(data_load_dir / _tagged_name(post_id, tag), data_preview)

            research_preview = research.preview_post(
                data_preview["post"],
                step="filter-researched",
                force=True,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache=use_research_fetch_cache,
                disable_bing_fallback=disable_bing_fallback,
            )
            _write_json(research_dir / _tagged_name(post_id, tag), research_preview)

            angles_preview = gen_angles.preview_post(
                research_preview["post"],
                allow_fallback=allow_angles_fallback,
            )
            _write_json(angles_dir / _tagged_name(post_id, tag), angles_preview)

            stego_result = stego.encode(
                payload=resolved_payload,
                post=angles_preview["post"],
                tag=tag,
            )
            output_artifact = n8n_save_object_body(stego_result)
            output_path = output_dir / _tagged_name(post_id, tag)
            _write_json(output_path, output_artifact)

            if mirror_tagged_artifacts:
                backend.save_object_local(
                    angles_preview["post"],
                    step="angles-step",
                    filename=_tagged_name(post_id, tag),
                )
                backend.save_object_local(
                    output_artifact,
                    step="final-step",
                    filename=_tagged_name(post_id, tag),
                )

            visible_stego_text = strip_invisible_payload(str(stego_result.get("stego_text", "")))
            hidden_payload = extract_invisible_payload(str(stego_result.get("stego_text", "")))
            comment_bodies = [
                str(comment.get("body", ""))
                for comment in source_post.get("comments", [])
                if isinstance(comment, dict) and isinstance(comment.get("body"), str)
            ]
            entries.append(
                {
                    "post_id": post_id,
                    "title": source_post.get("title"),
                    "url": source_post.get("url"),
                    "succeeded": bool(stego_result.get("succeeded")),
                    "selected_angle": _selected_angle_summary(stego_result),
                    "visible_stego_text": visible_stego_text,
                    "hidden_payload_bytes": len(hidden_payload.encode("utf-8"))
                    if isinstance(hidden_payload, str)
                    else None,
                    "comment_bodies": comment_bodies,
                    "data_load_report": data_preview.get("report"),
                    "research_report": research_preview.get("report"),
                    "gen_angles_report": angles_preview.get("report"),
                    "stego_breakdown": stego_result.get("breakdown"),
                    "output_file": str(output_path),
                }
            )
        except Exception as exc:
            failures.append({"post_id": post_id, "error": f"{type(exc).__name__}: {exc}"})

    if not entries:
        raise RuntimeError(f"All selected posts failed. Failures: {failures}")

    divergence = run_divergence_metrics(output_dir, dataset_dir, metrics_dir)
    metrics_report = divergence["report"]
    inspection_path = resolved_run_dir / "inspection.md"
    summary = {
        "tag": tag,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_dir": str(resolved_run_dir),
        "payload_length_bytes": len(resolved_payload.encode("utf-8")),
        "post_ids_requested": list(resolved_post_ids),
        "posts_succeeded": len(entries),
        "posts_failed": len(failures),
        "failures": failures,
        "metrics_report_path": divergence["report_path"],
        "entries": entries,
    }
    _write_json(resolved_run_dir / "summary.json", summary)
    inspection_path.write_text(
        _inspection_markdown(
            tag=tag,
            payload=resolved_payload,
            run_dir=resolved_run_dir,
            metrics_report_path=divergence["report_path"],
            metrics_report=metrics_report,
            entries=entries,
        ),
        encoding="utf-8",
    )
    return {
        "run_dir": str(resolved_run_dir),
        "summary_path": str((resolved_run_dir / "summary.json").resolve()),
        "inspection_path": str(inspection_path.resolve()),
        "metrics_report_path": divergence["report_path"],
        "metrics_report": metrics_report,
        "posts_succeeded": len(entries),
        "posts_failed": len(failures),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run data_load -> research -> gen_angles -> stego locally for a tagged sample set, "
            "then compute KLD on just that run and write an inspection report."
        )
    )
    parser.add_argument("--tag", required=True, help="Run tag used in filenames and run folder.")
    parser.add_argument(
        "--payload",
        default=None,
        help="Payload to encode. If omitted, uses the workflow default payload.",
    )
    parser.add_argument(
        "--post-id",
        action="append",
        default=[],
        help="Specific post id to run. Repeat for multiple posts. If omitted, uses --count/--offset.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of source posts to select when --post-id is omitted.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset into filter-url-unresolved selection when --post-id is omitted.",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Optional explicit directory for the dedicated run output.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing run directory for the same tag.",
    )
    parser.add_argument(
        "--no-data-load-cache",
        action="store_true",
        help="Disable URL fetch cache during data_load preview.",
    )
    parser.add_argument(
        "--no-terms-cache",
        action="store_true",
        help="Disable research term cache reads.",
    )
    parser.add_argument(
        "--no-persist-terms-cache",
        action="store_true",
        help="Do not persist research term cache writes.",
    )
    parser.add_argument(
        "--no-research-fetch-cache",
        action="store_true",
        help="Disable research page fetch cache reads.",
    )
    parser.add_argument(
        "--allow-angles-fallback",
        action="store_true",
        help="Allow the gen_angles fallback path if the primary path fails.",
    )
    parser.add_argument(
        "--disable-bing-fallback",
        action="store_true",
        help="Disable Bing fallback during research when Google quota hits.",
    )
    parser.add_argument(
        "--mirror-tagged-artifacts",
        action="store_true",
        help="Also write tagged angle and stego artifacts into the normal step dirs.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="JSONL log level for progress and summary lines.",
    )
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    result = run_tagged_e2e(
        tag=args.tag,
        payload=args.payload,
        post_ids=args.post_id,
        count=args.count,
        offset=args.offset,
        run_dir=Path(args.run_dir).resolve() if args.run_dir else None,
        overwrite=bool(args.overwrite),
        use_data_load_cache=not args.no_data_load_cache,
        use_terms_cache=not args.no_terms_cache,
        persist_terms_cache=not args.no_persist_terms_cache,
        use_research_fetch_cache=not args.no_research_fetch_cache,
        allow_angles_fallback=bool(args.allow_angles_fallback),
        disable_bing_fallback=bool(args.disable_bing_fallback),
        mirror_tagged_artifacts=bool(args.mirror_tagged_artifacts),
    )
    metrics = result["metrics_report"]
    logger.bind(component="TaggedE2E").info(
        "tagged_e2e_complete",
        run_dir=result["run_dir"],
        inspection_path=result["inspection_path"],
        summary_path=result["summary_path"],
        metrics_report_path=result["metrics_report_path"],
        posts_succeeded=result["posts_succeeded"],
        posts_failed=result["posts_failed"],
        primary_kl=metrics["primary_baseline_matched_post"][
            "average_kl_stego_vs_matched_post"
        ],
        secondary_kl=metrics["secondary_baseline_global_corpus"][
            "average_kl_stego_vs_global_corpus"
        ],
    )
    if result["failures"]:
        for failure in result["failures"]:
            logger.bind(component="TaggedE2E").error(
                "tagged_e2e_failure",
                post_id=failure["post_id"],
                error=failure["error"],
            )


if __name__ == "__main__":
    append_current_pid_to_log()
    main()
