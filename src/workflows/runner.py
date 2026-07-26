"""Workflow runner for orchestrating pipeline execution."""

import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, ConfigDict

from infrastructure.json_logging import get_trace_id
from infrastructure.workflow_run_tracker import has_active_run_for_command
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.config import isolated_workflow_config
from workflows.errors import DataLoadFetchError, NoUnprocessedPostsError
from workflows.pipelines.data_load import DataLoadPipeline
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline
from workflows.pipelines.receiver import ReceiverPipeline
from workflows.pipelines.research import ResearchPipeline, is_likely_google_quota_error
from workflows.pipelines.stego import StegoPipeline
from workflows.runner_diff_utils import collect_diff_paths, collect_mismatch_value_snippets
from workflows.runner_orchestration_utils import (
    clear_double_process_claim,
    double_process_cache_base_root,
    is_receiver_data_load_failure,
    isolated_workflow_config_for_side,
    normalized_angles_from_raw,
    persist_double_process_final_report,
    reconcile_stale_double_process_claim_vs_explicit,
    research_run_with_breakdown,
    run_stego_receiver_live_sim_once,
    try_read_double_process_claim,
    workflow_cache_paths,
    write_double_process_claim,
)
from workflows.runner_validate_post import (
    validation_failure_summary_for_log,
    validation_outcome_from_report,
)
from workflows.stages import (
    ANGLES_STEP,
    CONTEXT_STAGE_STEPS,
    CONTEXT_STAGES,
    DATA_LOAD_STEP,
    RESEARCH_STEP,
)
from workflows.utils.capacity_observability import log_workflow_capacity_observation
from workflows.utils.protocol_utils import stable_hash

_LOG = logger.bind(component="WorkflowRunner")


def _is_no_unprocessed_posts(exc: Exception) -> bool:
    """Normal end of a run-all loop rather than a fault.

    Type check first; the substring fallback keeps callers that raise a plain ValueError
    working, which several tests and older call sites still do.
    """
    if isinstance(exc, NoUnprocessedPostsError):
        return True
    return "No unprocessed posts found" in str(exc)


class _PrepBatchOutcome(BaseModel):
    """What one prep iteration contributed to the totals, and why the loop should stop.

    ``stop_reason`` is ``None`` while the loop should keep going. ``quota_detected`` is set
    only by the Google-search-quota stop, because that is the one stop reason that hands off
    to the stego phase rather than ending the run.
    """

    model_config = ConfigDict(frozen=True)

    data_load_processed: int = 0
    research_processed: int = 0
    gen_angles_processed: int = 0
    gen_angles_failed: int = 0
    stop_reason: str | None = None
    quota_detected: bool = False


class WorkflowRunner:
    """Owns pipeline instances, fetch-failure counters, and orchestration entry points.

    Logs use module ``_LOG`` so instances created via ``__new__`` (tests) still emit
    with component ``WorkflowRunner`` without running ``__init__``.
    """

    def __init__(
        self,
        *,
        backend: BackendAPIAdapter | None = None,
        data_load: DataLoadPipeline | None = None,
        research: ResearchPipeline | None = None,
        gen_angles: GenAnglesPipeline | None = None,
        stego: StegoPipeline | None = None,
        decode: DecodePipeline | None = None,
        receiver: ReceiverPipeline | None = None,
        gen_terms: GenSearchTermsPipeline | None = None,
    ) -> None:
        """Every dependency is injectable; omitting one builds the production default.

        Zero-argument construction is unchanged, so existing call sites and scripts keep
        working -- the parameters exist so tests and the app factory can substitute fakes
        without reaching for ``__new__``.
        """
        self.backend = backend or BackendAPIAdapter()
        self.data_load = data_load or DataLoadPipeline()
        self.research = research or ResearchPipeline()
        self.gen_angles = gen_angles or GenAnglesPipeline()
        self.stego = stego or StegoPipeline()
        self.decode = decode or DecodePipeline()
        self.receiver = receiver or ReceiverPipeline()
        self.gen_terms = gen_terms or GenSearchTermsPipeline()
        # In-memory counters for data-load URL fetch failures by post id.
        # This resets when the API process restarts.
        self._fetch_fail_counts: dict[str, int] = {}

    @staticmethod
    def _emit(
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        if on_progress is None:
            return
        try:
            on_progress(event, payload)
        except Exception:
            # Progress reporting must never break workflow execution.
            return

    @staticmethod
    def _call_with_optional_progress(
        func: Callable[..., Any],
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        **kwargs: Any,
    ) -> Any:
        if on_progress is None:
            return func(**kwargs)
        try:
            return func(on_progress=on_progress, **kwargs)
        except TypeError as exc:
            # Tests may monkeypatch runner methods with simple lambdas.
            if "on_progress" not in str(exc):
                raise
            return func(**kwargs)

    def _artifact_path(self, step: str, post_id: str) -> Path:
        _, dest_dir = self.backend.config.get_step_dirs(step)
        return dest_dir / f"{post_id}.json"

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _summarize_stage_payload(stage_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {"hash": stable_hash(payload)}
        if stage_name == "data_load":
            selftext = payload.get("selftext", "")
            summary.update(
                {
                    "selftext_hash": stable_hash(selftext),
                    "selftext_length": len(selftext) if isinstance(selftext, str) else 0,
                }
            )
            return summary
        if stage_name == "research":
            results = payload.get("search_results", [])
            summary.update(
                {
                    "search_results_hash": stable_hash(results),
                    "search_results_count": len(results) if isinstance(results, list) else 0,
                }
            )
            return summary
        if stage_name == "gen_angles":
            angles = payload.get("angles", [])
            summary.update(
                {
                    "angles_hash": stable_hash(angles),
                    "angles_count": len(angles) if isinstance(angles, list) else 0,
                    "options_count": payload.get("options_count"),
                }
            )
            return summary
        return summary

    def preview_data_load_post(
        self,
        post_id: str,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        return self.data_load.preview_post_id(
            post_id=post_id,
            step=DATA_LOAD_STEP,
            use_cache=use_cache,
        )

    def preview_research_post(
        self,
        post_id: str,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        source_post: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        file_name = f"{post_id}.json"
        post = source_post or self.backend.get_post_local(file_name, RESEARCH_STEP)
        return self.research.preview_post(
            post=post,
            step=RESEARCH_STEP,
            force=True,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )

    def preview_gen_angles_post(
        self,
        post_id: str,
        source_post: dict[str, Any] | None = None,
        allow_fallback: bool = False,
    ) -> dict[str, Any]:
        file_name = f"{post_id}.json"
        post = source_post or self.backend.get_post_local(file_name, ANGLES_STEP)
        return self.gen_angles.preview_post(post=post, allow_fallback=allow_fallback)

    def run_data_load(
        self,
        count: int = 100,
        offset: int = 0,
        batch_size: int = 5,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[dict]:
        """Run DataLoad pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "data-load", "count": count, "offset": offset, "batch_size": batch_size},
        )
        t0 = time.perf_counter()
        results = self.data_load.process_posts(
            step=DATA_LOAD_STEP,
            count=count,
            offset=offset,
            batch_size=batch_size,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _LOG.bind(trace_id=get_trace_id()).info(
            "workflow_data_load_complete",
            elapsed_ms=elapsed_ms,
            processed_count=len(results),
            count=count,
            offset=offset,
            batch_size=batch_size,
        )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "data-load",
                "processed_count": len(results),
                "elapsed_ms": elapsed_ms,
            },
        )
        return results

    def run_research(
        self,
        count: int = 1,
        offset: int = 0,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
        include_breakdown: bool = False,
        disable_bing_fallback: bool = False,
    ) -> Any:
        """Run Research pipeline."""
        trace_id = str(uuid4())
        rid = _LOG.bind(trace_id=trace_id)
        t0 = time.perf_counter()
        rid.info(
            "workflow_research_run_begin",
            event="research_timing",
            count=count,
            offset=offset,
            include_breakdown=include_breakdown,
        )
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "research", "count": count, "offset": offset},
        )
        results = self.research.process_posts(
            step=RESEARCH_STEP,
            count=count,
            offset=offset,
            include_breakdown=include_breakdown,
            disable_bing_fallback=disable_bing_fallback,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        processed_count = len(results)
        rid.info(
            "workflow_research_run_complete",
            event="research_timing",
            elapsed_ms=elapsed_ms,
            processed_count=processed_count,
            include_breakdown=include_breakdown,
        )
        payload_out: Any = results
        if include_breakdown:
            entries = list(self.research.last_research_breakdown_posts)
            payload_out = research_run_with_breakdown(
                posts=results,
                breakdown_entries=entries,
                batch_elapsed_ms=elapsed_ms,
                requested_count=count,
                offset=offset,
                runner_trace_id=trace_id,
            )
            rid.info(
                "research_breakdown_batch",
                event="research_breakdown_batch",
                batch_elapsed_ms=elapsed_ms,
                processed_count=processed_count,
                preview_total_ms_sum=payload_out["breakdown"]["batch"]["preview_total_ms_sum"],
                requested_count=count,
                offset=offset,
            )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "research",
                "processed_count": processed_count,
                "elapsed_ms": elapsed_ms,
            },
        )
        return payload_out

    def _emit_prep_stage_done(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        iteration: int,
        stage: str,
        processed_count: int,
        elapsed_ms: int,
        failed_count: int | None = None,
    ) -> None:
        """Emit one ``prep_stage_done``; ``failed_count`` is only carried by the angles stage."""
        payload: dict[str, Any] = {
            "phase": "prep",
            "iteration": iteration,
            "stage": stage,
            "processed_count": processed_count,
        }
        if failed_count is not None:
            payload["failed_count"] = failed_count
        payload["elapsed_ms"] = elapsed_ms
        self._emit(on_progress, "prep_stage_done", payload)

    def _emit_prep_batch_summary(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        iteration: int,
        outcome: "_PrepBatchOutcome",
    ) -> None:
        """Emit the per-iteration ``prep_batch_summary``. Not emitted when quota stops the batch."""
        self._emit(
            on_progress,
            "prep_batch_summary",
            {
                "phase": "prep",
                "iteration": iteration,
                "data_load_processed": outcome.data_load_processed,
                "research_processed": outcome.research_processed,
                "gen_angles_processed": outcome.gen_angles_processed,
                "gen_angles_failed": outcome.gen_angles_failed,
                "prepared_posts_in_batch": outcome.gen_angles_processed,
                "produced_prepared_posts": outcome.gen_angles_processed > 0,
            },
        )

    def _run_prep_data_load_stage(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        iteration: int,
        batch_count: int,
        batch_size: int,
    ) -> list[dict[str, Any]]:
        """Load one batch of posts and report how long it took."""
        t0 = time.perf_counter()
        results = self.run_data_load(count=batch_count, offset=0, batch_size=batch_size)
        self._emit_prep_stage_done(
            on_progress,
            iteration=iteration,
            stage="data-load",
            processed_count=len(results),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
        return results

    def _run_prep_research_stage(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        iteration: int,
        posts: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Research one batch. Returns ``None`` once Google search quota is hit.

        A quota error is the designed stop signal for the prep phase, so it is reported and
        swallowed. Every other failure propagates.
        """
        t0 = time.perf_counter()
        try:
            results = self.research.process_post_objects(
                posts=posts,
                step=RESEARCH_STEP,
                disable_bing_fallback=True,
            )
        except Exception as exc:
            if not is_likely_google_quota_error(exc):
                raise
            self._emit(
                on_progress,
                "quota_detected",
                {
                    "phase": "prep",
                    "iteration": iteration,
                    "stage": "research",
                    "message": str(exc),
                },
            )
            return None
        self._emit_prep_stage_done(
            on_progress,
            iteration=iteration,
            stage="research",
            processed_count=len(results),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
        return results

    def _run_prep_angles_stage(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        iteration: int,
        posts: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Generate angles for one batch; returns ``(processed_count, failed_count)``."""
        t0 = time.perf_counter()
        results = self.gen_angles.process_post_objects(posts=posts, step=ANGLES_STEP)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        summary = dict(getattr(self.gen_angles, "_last_batch_summary", {}) or {})
        processed = len(results)
        failed = int(summary.get("failed_count", 0) or 0)
        self._emit_prep_stage_done(
            on_progress,
            iteration=iteration,
            stage="gen-angles",
            processed_count=processed,
            failed_count=failed,
            elapsed_ms=elapsed_ms,
        )
        return processed, failed

    def _run_prep_batch(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        iteration: int,
        batch_count: int,
        batch_size: int,
    ) -> "_PrepBatchOutcome":
        """Run data-load → research → angles once, reporting what it did and whether to stop."""
        data_results = self._run_prep_data_load_stage(
            on_progress,
            iteration=iteration,
            batch_count=batch_count,
            batch_size=batch_size,
        )
        if not data_results:
            outcome = _PrepBatchOutcome(stop_reason="no_more_posts")
            self._emit_prep_batch_summary(on_progress, iteration=iteration, outcome=outcome)
            return outcome

        research_results = self._run_prep_research_stage(
            on_progress,
            iteration=iteration,
            posts=data_results,
        )
        if research_results is None:
            return _PrepBatchOutcome(
                data_load_processed=len(data_results),
                stop_reason="google_search_quota_detected",
                quota_detected=True,
            )

        angles_processed, angles_failed = self._run_prep_angles_stage(
            on_progress,
            iteration=iteration,
            posts=research_results,
        )
        outcome = _PrepBatchOutcome(
            data_load_processed=len(data_results),
            research_processed=len(research_results),
            gen_angles_processed=angles_processed,
            gen_angles_failed=angles_failed,
        )
        self._emit_prep_batch_summary(on_progress, iteration=iteration, outcome=outcome)
        return outcome

    def _run_prep_phase(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        batch_count: int,
        batch_size: int,
    ) -> dict[str, Any]:
        """Prepare posts batch after batch until the corpus runs dry or Google quota is hit."""
        totals = {
            "data_load_processed": 0,
            "research_processed": 0,
            "gen_angles_processed": 0,
            "gen_angles_failed": 0,
            "prepared_posts": 0,
        }
        iterations = 0
        while True:
            iterations += 1
            self._emit(
                on_progress,
                "prep_batch_start",
                {
                    "phase": "prep",
                    "iteration": iterations,
                    "batch_count": batch_count,
                    "batch_size": batch_size,
                },
            )
            outcome = self._run_prep_batch(
                on_progress,
                iteration=iterations,
                batch_count=batch_count,
                batch_size=batch_size,
            )
            totals["data_load_processed"] += outcome.data_load_processed
            totals["research_processed"] += outcome.research_processed
            totals["gen_angles_processed"] += outcome.gen_angles_processed
            totals["gen_angles_failed"] += outcome.gen_angles_failed
            totals["prepared_posts"] += outcome.gen_angles_processed
            if outcome.stop_reason is not None:
                break
        return {
            "iterations": iterations,
            **totals,
            "quota_detected": outcome.quota_detected,
            "stop_reason": outcome.stop_reason,
        }

    def _emit_stego_batch_summary(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        processed_count: int,
        succeeded_count: int,
        failed_count: int,
        stop_reason: str | None = None,
    ) -> None:
        """Emit ``stego_batch_summary``; ``stop_reason`` only appears on the terminal emit."""
        payload: dict[str, Any] = {
            "phase": "stego",
            "processed_count": processed_count,
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
        }
        if stop_reason is not None:
            payload["stop_reason"] = stop_reason
        self._emit(on_progress, "stego_batch_summary", payload)

    @staticmethod
    def _stego_result_post_id(result: dict[str, Any]) -> str | None:
        """Pull the post id out of a stego result, or ``None`` when it carries no usable id."""
        post_obj = result.get("post")
        if isinstance(post_obj, dict) and post_obj.get("id") is not None:
            return str(post_obj.get("id"))
        return None

    def _next_stego_result(self, *, tag: str, payload: str | None) -> dict[str, Any] | None:
        """Stego the next prepared post, or ``None`` once the queue is empty.

        An empty queue is the normal end of a drain, so it is translated out of the
        ``ValueError`` the pipeline raises. Any other ``ValueError`` is a real fault.
        """
        try:
            return self.stego.process_post(
                post_id=None,
                payload=payload,
                tag=tag,
                list_offset=0,
            )
        except ValueError as exc:
            if _is_no_unprocessed_posts(exc):
                return None
            raise

    def _drain_prepared_posts(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        tag: str,
        payload: str | None,
    ) -> tuple[list[dict[str, Any]], int, int, str]:
        """Stego every prepared post until the queue empties or a post fails twice.

        Returns ``(results, succeeded_count, failed_count, stop_reason)``.
        """
        results: list[dict[str, Any]] = []
        succeeded_count = 0
        failed_count = 0
        seen_failed_post_ids: set[str] = set()
        while True:
            result = self._next_stego_result(tag=tag, payload=payload)
            if result is None:
                return results, succeeded_count, failed_count, "no_unprocessed_posts"

            results.append(result)
            post_id_value = self._stego_result_post_id(result)
            succeeded = bool(result.get("succeeded"))
            self._emit(
                on_progress,
                "stego_post_done",
                {
                    "phase": "stego",
                    "post_id": post_id_value,
                    "succeeded": succeeded,
                    "retry_count": int(result.get("retry_count", 0)),
                    "processed_count": len(results),
                },
            )

            if succeeded:
                succeeded_count += 1
            else:
                failed_count += 1
                stop_reason = self._stego_failure_stop_reason(
                    post_id_value,
                    seen_failed_post_ids,
                )
                if stop_reason is not None:
                    self._emit_stego_batch_summary(
                        on_progress,
                        processed_count=len(results),
                        succeeded_count=succeeded_count,
                        failed_count=failed_count,
                        stop_reason=stop_reason,
                    )
                    return results, succeeded_count, failed_count, stop_reason

            self._emit_stego_batch_summary(
                on_progress,
                processed_count=len(results),
                succeeded_count=succeeded_count,
                failed_count=failed_count,
            )

    @staticmethod
    def _stego_failure_stop_reason(post_id_value: str | None, seen: set[str]) -> str | None:
        """Decide whether a failed post ends the drain; records the id when it does not.

        An id-less failure cannot be deduplicated, and a second failure of the same post means
        the queue is not draining -- either way, continuing would spin.
        """
        if not post_id_value:
            return "failed_post_without_id"
        if post_id_value in seen:
            return "repeat_failed_post"
        seen.add(post_id_value)
        return None

    def _run_stego_phase(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        tag: str,
        payload: str | None,
    ) -> dict[str, Any]:
        """Run the post-quota stego phase over everything prep managed to prepare."""
        self._emit(on_progress, "phase_start", {"phase": "stego"})
        self._emit(
            on_progress,
            "stego_batch_start",
            {
                "phase": "stego",
                "tag": tag,
                "list_offset": 0,
            },
        )
        t0 = time.perf_counter()
        results, succeeded_count, failed_count, stop_reason = self._drain_prepared_posts(
            on_progress,
            tag=tag,
            payload=payload,
        )
        stego_result = {
            "run_all": True,
            "tag": tag,
            "list_offset": 0,
            "processed_count": len(results),
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
            "stopped_reason": stop_reason,
            "results": results,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
        self._emit(
            on_progress,
            "phase_done",
            {
                "phase": "stego",
                "processed_count": stego_result["processed_count"],
                "succeeded_count": stego_result["succeeded_count"],
                "failed_count": stego_result["failed_count"],
                "stop_reason": stego_result["stopped_reason"],
                "elapsed_ms": stego_result["elapsed_ms"],
            },
        )
        return stego_result

    @staticmethod
    def _stego_phase_not_started(tag: str) -> dict[str, Any]:
        """The stego block reported when prep finished without ever hitting quota."""
        return {
            "run_all": True,
            "tag": tag,
            "list_offset": 0,
            "processed_count": 0,
            "succeeded_count": 0,
            "failed_count": 0,
            "stopped_reason": "not_started_quota_not_detected",
            "results": [],
            "elapsed_ms": 0,
        }

    def run_prep_until_google_quota_then_stego(
        self,
        *,
        tag: str,
        batch_count: int = 1,
        batch_size: int = 5,
        payload: str | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Prepare posts until Google search quota runs out, then stego what was prepared.

        Quota exhaustion is the intended hand-off signal, not an error: prep uses the search
        budget, and the stego phase needs none of it. If prep stops for any other reason the
        stego phase never starts.
        """
        self._emit(
            on_progress,
            "workflow_start",
            {
                "workflow": "prep-until-google-quota-then-stego",
                "tag": tag,
                "payload_provided": bool(payload),
                "batch_count": batch_count,
                "batch_size": batch_size,
            },
        )
        self._emit(on_progress, "phase_start", {"phase": "prep"})
        prep_result = self._run_prep_phase(
            on_progress,
            batch_count=batch_count,
            batch_size=batch_size,
        )
        self._emit(on_progress, "phase_done", {"phase": "prep", **prep_result})

        if not prep_result["quota_detected"]:
            return {
                "succeeded": True,
                "tag": tag,
                "prep": prep_result,
                "stego": self._stego_phase_not_started(tag),
                "phase_transition": None,
            }

        phase_transition = {
            "from_phase": "prep",
            "to_phase": "stego",
            "reason": "google_search_quota_detected",
        }
        self._emit(on_progress, "phase_transition", phase_transition)
        return {
            "succeeded": True,
            "tag": tag,
            "prep": prep_result,
            "stego": self._run_stego_phase(on_progress, tag=tag, payload=payload),
            "phase_transition": phase_transition,
        }

    def run_gen_angles(
        self,
        count: int = 1,
        offset: int = 0,
        tag: str | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[dict]:
        """Run GenAngles pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "gen-angles", "count": count, "offset": offset, "tag": tag},
        )
        t0 = time.perf_counter()
        results = self.gen_angles.process_posts(
            step=ANGLES_STEP,
            count=count,
            offset=offset,
            tag=tag,
        )
        batch_ms = int((time.perf_counter() - t0) * 1000)
        summary = dict(getattr(self.gen_angles, "_last_batch_summary", {}) or {})
        tid = get_trace_id()
        _LOG.bind(trace_id=tid if tid else "").info(
            "workflow_gen_angles_batch_timing",
            elapsed_ms=batch_ms,
            processed_count=len(results),
            requested_count=summary.get("requested_count"),
            listed_count=summary.get("listed_count"),
            loaded_count=summary.get("loaded_count"),
            load_failed_count=summary.get("load_failed_count"),
            processing_failed_count=summary.get("processing_failed_count"),
            failed_count=summary.get("failed_count"),
            degraded=bool(summary.get("failed_count", 0)),
        )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "gen-angles",
                "processed_count": len(results),
                "elapsed_ms": batch_ms,
                "requested_count": summary.get("requested_count"),
                "tag": summary.get("tag"),
                "listed_count": summary.get("listed_count"),
                "loaded_count": summary.get("loaded_count"),
                "load_failed_count": summary.get("load_failed_count"),
                "processing_failed_count": summary.get("processing_failed_count"),
                "failed_count": summary.get("failed_count"),
                "degraded": bool(summary.get("failed_count", 0)),
            },
        )
        return results

    def run_stego(
        self,
        post_id: str | None = None,
        payload: str | None = None,
        tag: str | None = None,
        list_offset: int = 1,
        run_all: bool = False,
        max_posts: int | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run Stego pipeline."""
        max_posts_cap: int | None = max_posts if max_posts is not None and max_posts >= 1 else None
        if run_all and post_id:
            raise ValueError("'post_id' cannot be combined with run_all=true")

        self._emit(
            on_progress,
            "stage_start",
            {
                "stage": "stego",
                "post_id": post_id,
                "tag": tag,
                "list_offset": list_offset,
                "run_all": run_all,
                "max_posts": max_posts_cap,
            },
        )
        if not run_all:
            t0 = time.perf_counter()
            result = self.stego.process_post(
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=list_offset,
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._emit(
                on_progress,
                "stage_done",
                {
                    "stage": "stego",
                    "succeeded": bool(result.get("succeeded")),
                    "retry_count": int(result.get("retry_count", 0)),
                    "elapsed_ms": elapsed_ms,
                },
            )
            return result

        results: list[dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        seen_failed_post_ids: set[str] = set()
        stop_reason = "no_unprocessed_posts"
        t_run_all = time.perf_counter()

        while True:
            if max_posts_cap is not None and len(results) >= max_posts_cap:
                stop_reason = "max_posts_reached"
                break
            try:
                result = self.stego.process_post(
                    post_id=None,
                    payload=payload,
                    tag=tag,
                    list_offset=list_offset,
                )
            except ValueError as exc:
                if _is_no_unprocessed_posts(exc):
                    stop_reason = "no_unprocessed_posts"
                    break
                raise

            results.append(result)
            succeeded = bool(result.get("succeeded"))
            post_obj = result.get("post")
            post_id_value = (
                str(post_obj.get("id"))
                if isinstance(post_obj, dict) and post_obj.get("id") is not None
                else None
            )
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "stego",
                    "run_all": True,
                    "processed_count": len(results),
                    "post_id": post_id_value,
                    "succeeded": succeeded,
                    "retry_count": int(result.get("retry_count", 0)),
                },
            )

            if succeeded:
                success_count += 1
                continue

            failure_count += 1
            if not post_id_value:
                stop_reason = "failed_post_without_id"
                break
            if post_id_value in seen_failed_post_ids:
                stop_reason = "repeat_failed_post"
                break
            seen_failed_post_ids.add(post_id_value)

        result = {
            "run_all": True,
            "tag": tag,
            "list_offset": list_offset,
            "max_posts": max_posts_cap,
            "processed_count": len(results),
            "succeeded_count": success_count,
            "failed_count": failure_count,
            "stopped_reason": stop_reason,
            "results": results,
        }
        run_all_elapsed_ms = int((time.perf_counter() - t_run_all) * 1000)
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "stego",
                "run_all": True,
                "processed_count": len(results),
                "succeeded_count": success_count,
                "failed_count": failure_count,
                "stopped_reason": stop_reason,
                "elapsed_ms": run_all_elapsed_ms,
            },
        )
        return result

    def run_decode(
        self,
        stego_text: str,
        angles: list[dict[str, Any]],
        few_shots: list[dict[str, Any]] | None = None,
        strict_mode: bool = False,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> int | None:
        """Run Decode pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "decode", "angles_count": len(angles)},
        )
        decoded_idx = self.decode.decode(
            stego_text=stego_text,
            angles=angles,
            few_shots=few_shots,
            strict_mode=strict_mode,
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "decode", "decoded_index": decoded_idx},
        )
        return decoded_idx

    def run_receiver(
        self,
        post: dict[str, Any],
        sender_user_id: str,
        *,
        use_fetch_cache: bool = True,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache_research: bool = True,
        allow_fallback: bool = False,
        compressed_full: str | None = None,
        max_padding_bits: int = 256,
        fail_on_context_drift: bool = True,
        strict_decode: bool = False,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Rebuild context on the receiver and recover the stego payload."""
        self._emit(
            on_progress,
            "stage_start",
            {
                "stage": "receiver",
                "post_id": post.get("id"),
                "sender_user_id": sender_user_id,
            },
        )
        try:
            result = self.receiver.run(
                post,
                sender_user_id,
                use_fetch_cache=use_fetch_cache,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache_research=use_fetch_cache_research,
                allow_fallback=allow_fallback,
                compressed_full=compressed_full,
                max_padding_bits=max_padding_bits,
                fail_on_context_drift=fail_on_context_drift,
                strict_decode=strict_decode,
                on_progress=on_progress,
            )
        except Exception:
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "receiver", "succeeded": False},
            )
            raise
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "receiver",
                "succeeded": bool(result.get("succeeded", False)),
                "post_id": post.get("id"),
            },
        )
        return result

    def run_multi_frame_stego(
        self,
        payload: str,
        posts: list[dict[str, Any]],
        *,
        max_frames_per_post: int = 3,
        tag: str | None = None,
    ) -> dict[str, Any]:
        return self.stego.encode_payload_frames(
            payload=payload,
            posts=posts,
            max_frames_per_post=max_frames_per_post,
            tag=tag,
        )

    def run_multi_frame_receiver(
        self,
        posts_or_profile_feed: list[dict[str, Any]],
        sender_user_id: str,
        *,
        ordered_frame_refs: list[dict[str, str]],
        payload_transform: str | None = None,
    ) -> dict[str, Any]:
        return self.receiver.run_multi_frame(
            posts_or_profile_feed,
            sender_user_id,
            ordered_frame_refs=ordered_frame_refs,
            payload_transform=payload_transform,
        )

    def run_stego_receiver_live_sim(
        self,
        sender_user_id: str,
        *,
        post_id: str | None = None,
        payload: str | None = None,
        tag: str | None = None,
        list_offset: int = 1,
        simulation_root: Path | None = None,
        max_post_attempts: int = 25,
        allow_fallback: bool = False,
        compressed_full: str | None = None,
        max_padding_bits: int = 256,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run stego then receiver with disjoint on-disk caches (cold receiver).

        When ``post_id`` is omitted, advances ``list_offset`` on receiver data-load
        HTML failures (and on stego failure) up to ``max_post_attempts`` tries.
        """
        uid = sender_user_id.strip()
        if not uid:
            raise ValueError("'sender_user_id' must be non-empty")

        base = simulation_root or Path(tempfile.mkdtemp(prefix=f"live_sim_{uuid4().hex}_"))
        base = base.resolve()
        multi = post_id is None
        attempts = max(1, max_post_attempts) if multi else 1
        skipped: list[dict[str, Any]] = []

        for attempt_idx in range(attempts):
            stego_off = list_offset + attempt_idx
            try:
                one = run_stego_receiver_live_sim_once(
                    uid=uid,
                    post_id=post_id,
                    stego_list_offset=stego_off if multi else list_offset,
                    payload=payload,
                    tag=tag,
                    base=base,
                    attempt_idx=attempt_idx,
                    multi_post=multi,
                    allow_fallback=allow_fallback,
                    compressed_full=compressed_full,
                    max_padding_bits=max_padding_bits,
                    on_progress=on_progress,
                )
            except Exception as exc:
                if multi and (
                    is_receiver_data_load_failure(exc) or is_likely_google_quota_error(exc)
                ):
                    stage = (
                        "receiver_data_load"
                        if is_receiver_data_load_failure(exc)
                        else "search_quota"
                    )
                    _LOG.info(
                        "live_sim_skip_post stage={} attempt={} offset={} err={}",
                        stage,
                        attempt_idx,
                        stego_off,
                        str(exc)[:200],
                    )
                    skipped.append(
                        {
                            "stage": stage,
                            "list_offset": stego_off,
                            "error": str(exc),
                        }
                    )
                    continue
                raise

            one["skipped_posts"] = list(skipped)
            if one.get("succeeded"):
                return one

            if multi:
                skipped.append(
                    {
                        "stage": "stego",
                        "list_offset": stego_off,
                        "stego": one.get("stego"),
                    }
                )
                continue

            return one

        return {
            "succeeded": False,
            "stage": "exhausted_attempts",
            "error": "No post succeeded within max_post_attempts",
            "stego": None,
            "receiver": None,
            "simulation": {"root": str(base)},
            "skipped_posts": skipped,
        }

    def run_gen_search_terms(
        self,
        post_id: str,
        post_title: str | None = None,
        post_text: str | None = None,
        post_url: str | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[str]:
        """Run GenSearchTerms pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "gen-terms", "post_id": post_id},
        )
        report = self.gen_terms.preview_generation(
            post_id=post_id,
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
        )
        terms = list(report.get("terms", []))
        stage_done = {
            "stage": "gen-terms",
            "terms_count": len(terms),
            "terms_hash": report.get("terms_hash"),
            "used_cache": report.get("used_cache"),
            "cache_hit": report.get("cache_hit"),
            "retry_count": report.get("retry_count"),
            "elapsed_ms": report.get("elapsed_ms"),
            "parse_mode": report.get("parse_mode"),
            "degraded": bool(report.get("error")),
        }
        if report.get("error"):
            stage_done["error"] = report.get("error")
            stage_done["error_kind"] = report.get("error_kind")
            stage_done["http_status"] = report.get("http_status")
        self._emit(
            on_progress,
            "stage_done",
            stage_done,
        )
        return terms

    def validate_post_pipeline(
        self,
        post_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
        use_terms_cache: bool = False,
        persist_terms_cache: bool = False,
        use_fetch_cache: bool = False,
        allow_angles_fallback: bool = False,
    ) -> dict[str, Any]:
        """
        Replay the live protocol for one post and compare with saved stage artifacts.

        Args:
            post_id: Post identifier without `.json`
            on_progress: Optional progress callback

        Returns:
            Validation report with per-stage strict equality results.
        """
        if not post_id or not post_id.strip():
            raise ValueError("'post_id' must be a non-empty string")
        post_id = post_id.strip()
        trace_id = get_trace_id() or str(uuid4())
        log = _LOG.bind(trace_id=trace_id)

        baseline: dict[str, dict[str, Any]] = {}
        for stage_name, step in CONTEXT_STAGE_STEPS.items():
            path = self._artifact_path(step, post_id)
            if not path.exists():
                raise FileNotFoundError(
                    f"Baseline artifact missing for stage '{stage_name}': {path}"
                )
            baseline[stage_name] = self._load_json(path)

        self._emit(
            on_progress,
            "stage_start",
            {"stage": "validate-post", "post_id": post_id},
        )
        stage_errors: dict[str, str] = {}
        rerun_payloads: dict[str, dict[str, Any]] = {}
        protocol_reports: dict[str, dict[str, Any]] = {}

        self._emit(
            on_progress,
            "stage_progress",
            {"stage": "validate-post", "event": "rerun_data_load", "post_id": post_id},
        )
        try:
            data_load_preview = self.preview_data_load_post(
                post_id=post_id,
                use_cache=use_fetch_cache,
            )
            rerun_payloads["data_load"] = data_load_preview["post"]
            protocol_reports["data_load"] = data_load_preview["report"]
            if not protocol_reports["data_load"].get("fetch_success"):
                stage_errors["data_load"] = str(
                    protocol_reports["data_load"].get("error") or "data-load fetch failed"
                )
        except Exception as exc:
            stage_errors["data_load"] = str(exc)

        if "data_load" not in stage_errors:
            self._emit(
                on_progress,
                "stage_progress",
                {"stage": "validate-post", "event": "rerun_research", "post_id": post_id},
            )
            try:
                research_preview = self.preview_research_post(
                    post_id=post_id,
                    source_post=rerun_payloads["data_load"],
                    use_terms_cache=use_terms_cache,
                    persist_terms_cache=persist_terms_cache,
                    use_fetch_cache=use_fetch_cache,
                )
                rerun_payloads["research"] = research_preview["post"]
                protocol_reports["research"] = research_preview["report"]
                if protocol_reports["research"].get("error"):
                    stage_errors["research"] = str(protocol_reports["research"]["error"])
            except Exception as exc:
                stage_errors["research"] = str(exc)

        if "data_load" not in stage_errors and "research" not in stage_errors:
            self._emit(
                on_progress,
                "stage_progress",
                {"stage": "validate-post", "event": "rerun_gen_angles", "post_id": post_id},
            )
            try:
                angles_preview = self.preview_gen_angles_post(
                    post_id=post_id,
                    source_post=rerun_payloads["research"],
                    allow_fallback=allow_angles_fallback,
                )
                rerun_payloads["gen_angles"] = angles_preview["post"]
                protocol_reports["gen_angles"] = angles_preview["report"]
            except Exception as exc:
                stage_errors["gen_angles"] = str(exc)

        if (
            "gen_angles" not in stage_errors
            and protocol_reports.get("research")
            and protocol_reports.get("gen_angles")
        ):
            log_workflow_capacity_observation(
                log,
                post_id=post_id,
                trace_id=trace_id,
                research_report=protocol_reports["research"],
                gen_angles_report=protocol_reports["gen_angles"],
            )

        steps_report: dict[str, dict[str, Any]] = {}
        valid = True
        upstream_failed = False
        for stage_name, step in CONTEXT_STAGE_STEPS.items():
            if upstream_failed:
                steps_report[stage_name] = {
                    "step": step,
                    "comparison": "skipped",
                    "matches": None,
                    "changed_keys": [],
                    "changed_key_snippets": [],
                    "comparison_note": (
                        "Not compared: a previous stage failed during rerun, so this stage was skipped. "
                        "This is not a baseline-vs-rerun mismatch."
                    ),
                    "error": "Skipped because an upstream stage failed during rerun",
                }
                valid = False
                continue

            if stage_name in stage_errors:
                steps_report[stage_name] = {
                    "step": step,
                    "comparison": "rerun_failed",
                    "matches": None,
                    "changed_keys": [],
                    "changed_key_snippets": [],
                    "comparison_note": (
                        "Live rerun did not finish successfully, so the saved artifact was not compared "
                        "to a fresh rerun. Treat this as an execution/network/provider failure, not a "
                        "protocol drift mismatch."
                    ),
                    "error": stage_errors[stage_name],
                    "baseline_summary": self._summarize_stage_payload(
                        stage_name, baseline[stage_name]
                    ),
                    "protocol_report": protocol_reports.get(stage_name),
                }
                valid = False
                upstream_failed = True
                continue

            rerun_payload = rerun_payloads[stage_name]
            baseline_payload = baseline[stage_name]
            matches = baseline_payload == rerun_payload
            changed_keys = [] if matches else collect_diff_paths(baseline_payload, rerun_payload)
            snippets = (
                [] if matches else collect_mismatch_value_snippets(baseline_payload, rerun_payload)
            )
            steps_report[stage_name] = {
                "step": step,
                "comparison": "match" if matches else "mismatch",
                "matches": matches,
                "changed_keys": changed_keys,
                "changed_key_snippets": snippets,
                "comparison_note": (
                    "Saved artifact and live rerun are byte-for-byte equal."
                    if matches
                    else (
                        "Mismatch: live rerun produced different JSON than the saved workflow artifact "
                        "for this stage (see changed_keys and changed_key_snippets). This indicates "
                        "protocol or data drift, not a failed rerun."
                    )
                ),
                "baseline_summary": self._summarize_stage_payload(stage_name, baseline_payload),
                "rerun_summary": self._summarize_stage_payload(stage_name, rerun_payload),
                "protocol_report": protocol_reports.get(stage_name),
            }
            valid = valid and matches

        outcome, validation_explanation = validation_outcome_from_report(
            valid=valid,
            steps_report=steps_report,
            stage_order=CONTEXT_STAGES,
        )
        failure_detail = validation_failure_summary_for_log(
            validation_outcome=outcome,
            steps_report=steps_report,
            stage_order=CONTEXT_STAGES,
        ).model_dump(exclude_none=True)

        result = {
            "post_id": post_id,
            "valid": valid,
            "validation_outcome": outcome,
            "validation_explanation": validation_explanation,
            "mode": "live_protocol_replay",
            "settings": {
                "use_terms_cache": use_terms_cache,
                "persist_terms_cache": persist_terms_cache,
                "use_fetch_cache": use_fetch_cache,
                "allow_angles_fallback": allow_angles_fallback,
            },
            "steps": steps_report,
        }
        log.bind(
            validation_outcome=outcome,
            validation_explanation=validation_explanation,
            failure_detail=failure_detail,
        ).info(
            "validate_post post_id={} valid={} outcome={} use_terms_cache={} use_fetch_cache={}",
            post_id,
            valid,
            outcome,
            use_terms_cache,
            use_fetch_cache,
        )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "validate-post",
                "post_id": post_id,
                "valid": valid,
            },
        )
        return result

    def _select_next_new_post(self, offset: int = 0) -> tuple[str, str]:
        """
        Pick one new post from filter-url-unresolved source queue.

        Returns:
            Tuple of (post_id, file_name)
        """
        listing = self.backend.posts_list(step=DATA_LOAD_STEP, count=1, offset=offset)
        file_names = listing.get("fileNames", [])
        if not file_names:
            raise ValueError(
                "No new posts available in datasets/news_cleaned that are not in datasets/news_url_fetched."
            )
        file_name = str(file_names[0])
        post_id = Path(file_name).stem
        if not post_id:
            raise ValueError(f"Invalid post filename returned by posts_list: {file_name!r}")
        return post_id, file_name

    def _resolve_double_process_post(self, explicit_post_id: str | None) -> tuple[str, str, bool]:
        """Return (post_id, file_name, resumed_from_claim). Raises if explicit id conflicts with claim."""
        reconcile_stale_double_process_claim_vs_explicit(
            explicit_post_id,
            has_active_double_process_run=has_active_run_for_command("double-process-new-post"),
        )
        claimed = try_read_double_process_claim()
        if explicit_post_id:
            if claimed and claimed[0] != explicit_post_id:
                raise ValueError(
                    "Active double-process claim exists for post_id="
                    f"{claimed[0]!r}; cannot target post_id={explicit_post_id!r}. "
                    "Finish the in-progress run or clear the claim."
                )
            if claimed:
                pid, fn = claimed
                return pid, fn, True
            fn = f"{explicit_post_id}.json"
            write_double_process_claim(explicit_post_id, fn)
            return explicit_post_id, fn, False
        if claimed:
            pid, fn = claimed
            return pid, fn, True
        pid, fn = self._select_next_new_post()
        write_double_process_claim(pid, fn)
        return pid, fn, False

    @staticmethod
    def _is_data_load_fetch_failure(exc: Exception) -> bool:
        if isinstance(exc, DataLoadFetchError):
            return True
        return "Failed to fetch URL content for post" in str(exc)

    def _record_fetch_failure(self, post_id: str) -> int:
        if not hasattr(self, "_fetch_fail_counts"):
            self._fetch_fail_counts = {}
        next_count = int(self._fetch_fail_counts.get(post_id, 0)) + 1
        self._fetch_fail_counts[post_id] = next_count
        return next_count

    def _clear_fetch_failure(self, post_id: str) -> None:
        if not hasattr(self, "_fetch_fail_counts"):
            self._fetch_fail_counts = {}
        self._fetch_fail_counts.pop(post_id, None)

    @staticmethod
    def _slim_substage_summary(summary: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "hash",
            "selftext_length",
            "search_results_count",
            "angles_count",
            "options_count",
        )
        return {k: summary[k] for k in keys if k in summary}

    def _double_process_substage_begin(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
    ) -> None:
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "substage_begin",
                "post_id": post_id,
                "pass": pass_num,
                "cache_mode": cache_mode,
                "pipeline_substage": pipeline_substage,
            },
        )
        _LOG.bind(
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
        ).info("workflow_progress_double_process_substage_begin")

    def _double_process_substage_end(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
        elapsed_ms: int,
        summary: dict[str, Any],
    ) -> None:
        slim = self._slim_substage_summary(summary)
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "substage_end",
                "post_id": post_id,
                "pass": pass_num,
                "cache_mode": cache_mode,
                "pipeline_substage": pipeline_substage,
                "elapsed_ms": elapsed_ms,
                "summary": slim,
            },
        )
        _LOG.bind(
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
            elapsed_ms=elapsed_ms,
            step_hash=summary.get("hash"),
        ).info("workflow_progress_double_process_substage_end")

    def _double_process_substage_failed(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
        elapsed_ms: int,
        exc: BaseException,
    ) -> None:
        err_text = str(exc)
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "substage_failed",
                "post_id": post_id,
                "pass": pass_num,
                "cache_mode": cache_mode,
                "pipeline_substage": pipeline_substage,
                "elapsed_ms": elapsed_ms,
                "error": err_text[:2000],
            },
        )
        _LOG.bind(
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
            elapsed_ms=elapsed_ms,
        ).opt(exception=exc).error("workflow_progress_double_process_substage_failed")

    def _run_timed_dp_substage(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
        run_fn: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._double_process_substage_begin(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
        )
        t0 = time.perf_counter()
        try:
            raw = run_fn()
        except BaseException as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._double_process_substage_failed(
                on_progress,
                post_id=post_id,
                pass_num=pass_num,
                cache_mode=cache_mode,
                pipeline_substage=pipeline_substage,
                elapsed_ms=elapsed_ms,
                exc=exc,
            )
            raise
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        summary = self._summarize_stage_payload(pipeline_substage, raw)
        self._double_process_substage_end(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
            elapsed_ms=elapsed_ms,
            summary=summary,
        )
        return raw, summary

    def _run_three_stage_post(
        self,
        post_id: str,
        *,
        use_terms_cache: bool,
        persist_terms_cache: bool,
        use_fetch_cache: bool,
        allow_angles_fallback: bool,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
        pass_num: int = 1,
        cache_mode: str = "unknown",
    ) -> dict[str, Any]:
        """Run data_load -> research -> gen_angles for one post ID."""
        _, data_summary = self._run_timed_dp_substage(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage="data_load",
            run_fn=lambda: self.data_load.process_post_id(
                post_id=post_id,
                step=DATA_LOAD_STEP,
                use_cache=use_fetch_cache,
            ),
        )
        _, research_summary = self._run_timed_dp_substage(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage="research",
            run_fn=lambda: self.research.process_post_id(
                post_id=post_id,
                step=RESEARCH_STEP,
                force=True,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache=use_fetch_cache,
            ),
        )
        _, angles_summary = self._run_timed_dp_substage(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage="gen_angles",
            run_fn=lambda: self.gen_angles.process_post_id(
                post_id=post_id,
                step=ANGLES_STEP,
                allow_fallback=allow_angles_fallback,
            ),
        )
        return {
            "settings": {
                "use_terms_cache": use_terms_cache,
                "persist_terms_cache": persist_terms_cache,
                "use_fetch_cache": use_fetch_cache,
                "allow_angles_fallback": allow_angles_fallback,
            },
            "steps": {
                "data_load": data_summary,
                "research": research_summary,
                "gen_angles": angles_summary,
            },
        }

    def run_double_process_new_post(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
        allow_angles_fallback: bool = False,
        explicit_post_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Process one new post twice through data_load -> research -> gen_angles.

        Each pass uses the same cache flags but its own persistent dedicated cache
        tree under ``DOUBLE_PROCESS_VALIDATION_ROOT`` (``pass_1/`` and ``pass_2/``).

        When ``explicit_post_id`` is set, that post is used instead of dequeuing
        from the new-post queue (unless resuming an existing claim for the same id).

        Writes ``active_post_claim.json`` under that root when dequeuing a post and
        removes it only after both passes finish and stage hashes are compared.
        On any error before that, the claim is refreshed so the next call keeps
        the same ``post_id`` until comparison completes. A JSON report is written
        under ``reports/`` when the run finishes (success or failure).
        """
        self._emit(
            on_progress,
            "workflow_start",
            {
                "workflow": "double-process-new-post",
                "allow_angles_fallback": allow_angles_fallback,
            },
        )
        post_id, file_name, resumed_from_claim = self._resolve_double_process_post(explicit_post_id)
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "selected_post",
                "post_id": post_id,
                "file_name": file_name,
                "offset": 0,
                "resumed_from_claim": resumed_from_claim,
            },
        )
        completed = False
        try:
            dp_base = double_process_cache_base_root()
            pass1_cfg = isolated_workflow_config_for_side(dp_base, "pass_1")
            pass2_cfg = isolated_workflow_config_for_side(dp_base, "pass_2")

            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "double-process-new-post",
                    "event": "pass_1_cached_start",
                    "post_id": post_id,
                },
            )
            t_pass1 = time.perf_counter()
            while True:
                try:
                    with isolated_workflow_config(pass1_cfg):
                        first_pass = self._run_three_stage_post(
                            post_id=post_id,
                            use_terms_cache=True,
                            persist_terms_cache=True,
                            use_fetch_cache=True,
                            allow_angles_fallback=allow_angles_fallback,
                            on_progress=on_progress,
                            pass_num=1,
                            cache_mode="pass_1",
                        )
                    self._clear_fetch_failure(post_id)
                    break
                except Exception as exc:
                    if not self._is_data_load_fetch_failure(exc):
                        raise
                    fail_count = self._record_fetch_failure(post_id)
                    self._emit(
                        on_progress,
                        "stage_progress",
                        {
                            "stage": "double-process-new-post",
                            "event": "fetch_failed",
                            "pass": 1,
                            "post_id": post_id,
                            "file_name": file_name,
                            "failure_count": fail_count,
                        },
                    )
                    _LOG.info(
                        "double_process_new_post pass 1 fetch failed post_id={} attempt={}; retrying",
                        post_id,
                        fail_count,
                    )
                    time.sleep(1.0)

            pass1_total_ms = int((time.perf_counter() - t_pass1) * 1000)
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "double-process-new-post",
                    "event": "pass_1_finished",
                    "post_id": post_id,
                    "pass": 1,
                    "cache_mode": "pass_1",
                    "elapsed_ms": pass1_total_ms,
                },
            )
            _LOG.bind(post_id=post_id, pass_num=1, elapsed_ms=pass1_total_ms).info(
                "workflow_progress_double_process_pass_finished"
            )
            first_pass["settings"]["cache_profile"] = "pass_1"
            first_pass["settings"]["cache_paths"] = workflow_cache_paths(pass1_cfg)

            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "double-process-new-post",
                    "event": "pass_2_validation_start",
                    "post_id": post_id,
                },
            )
            t_pass2 = time.perf_counter()
            while True:
                try:
                    with isolated_workflow_config(pass2_cfg):
                        second_pass = self._run_three_stage_post(
                            post_id=post_id,
                            use_terms_cache=True,
                            persist_terms_cache=True,
                            use_fetch_cache=True,
                            allow_angles_fallback=allow_angles_fallback,
                            on_progress=on_progress,
                            pass_num=2,
                            cache_mode="pass_2",
                        )
                    break
                except Exception as exc:
                    if not self._is_data_load_fetch_failure(exc):
                        raise
                    fail_count = self._record_fetch_failure(post_id)
                    self._emit(
                        on_progress,
                        "stage_progress",
                        {
                            "stage": "double-process-new-post",
                            "event": "fetch_failed",
                            "pass": 2,
                            "post_id": post_id,
                            "file_name": file_name,
                            "failure_count": fail_count,
                        },
                    )
                    _LOG.info(
                        "double_process_new_post pass 2 fetch failed post_id={} attempt={}; retrying",
                        post_id,
                        fail_count,
                    )
                    time.sleep(1.0)

            pass2_total_ms = int((time.perf_counter() - t_pass2) * 1000)
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "double-process-new-post",
                    "event": "pass_2_finished",
                    "post_id": post_id,
                    "pass": 2,
                    "cache_mode": "pass_2",
                    "elapsed_ms": pass2_total_ms,
                },
            )
            _LOG.bind(post_id=post_id, pass_num=2, elapsed_ms=pass2_total_ms).info(
                "workflow_progress_double_process_pass_finished"
            )
            second_pass["settings"]["cache_profile"] = "pass_2"
            second_pass["settings"]["cache_paths"] = workflow_cache_paths(pass2_cfg)

            comparison = {
                stage: first_pass["steps"][stage]["hash"] == second_pass["steps"][stage]["hash"]
                for stage in ("data_load", "research", "gen_angles")
            }
            result = {
                "mode": "double_process_new_post",
                "succeeded": True,
                "comparison_completed": True,
                "post_id": post_id,
                "source_file": file_name,
                "passes": {
                    "pass_1_cached": first_pass,
                    "pass_2_validation": second_pass,
                },
                "stage_hash_match": comparison,
            }
            _LOG.info(
                "double_process_new_post post_id={} data_load_match={} research_match={} gen_angles_match={}",
                post_id,
                comparison["data_load"],
                comparison["research"],
                comparison["gen_angles"],
            )
            self._emit(
                on_progress,
                "workflow_done",
                {
                    "workflow": "double-process-new-post",
                    "post_id": post_id,
                    "succeeded": True,
                    "comparison_completed": True,
                    "stage_hash_match": comparison,
                },
            )
            result["report_path"] = persist_double_process_final_report(dp_base, result)
            completed = True
            return result
        except Exception as exc:
            dp_base = double_process_cache_base_root()
            failure: dict[str, Any] = {
                "mode": "double_process_new_post",
                "succeeded": False,
                "comparison_completed": False,
                "post_id": post_id,
                "source_file": file_name,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
            failure["report_path"] = persist_double_process_final_report(dp_base, failure)
            _LOG.bind(post_id=post_id, file_name=file_name).exception(
                "double_process_new_post_failed"
            )
            self._emit(
                on_progress,
                "workflow_done",
                {
                    "workflow": "double-process-new-post",
                    "post_id": post_id,
                    "succeeded": False,
                    "comparison_completed": False,
                    "error_type": failure["error_type"],
                },
            )
            return failure
        finally:
            if completed:
                clear_double_process_claim()
            else:
                write_double_process_claim(post_id, file_name)

    def run_batch_angles_determinism(
        self,
        post_ids: list[str],
        *,
        step: str = ANGLES_STEP,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        Empirically test whether two fresh angle runs (no angles disk cache) match.

        Loads each post from ``step``, builds the same text dictionary as gen_angles,
        runs ``analyze_angles_from_texts(..., use_cache=False)`` twice, and compares
        normalized angle lists (same rules as production preview_post).
        """
        from content_acquisition.angles.angle_runner import analyze_angles_from_texts

        if not post_ids:
            raise ValueError("post_ids must contain at least one post id")

        self._emit(
            on_progress,
            "workflow_start",
            {
                "workflow": "batch-angles-determinism",
                "step": step,
                "post_count": len(post_ids),
            },
        )

        row_results: list[dict[str, Any]] = []

        for post_id_raw in post_ids:
            if not post_id_raw.strip():
                row_results.append(
                    {
                        "post_id": post_id_raw,
                        "error": "invalid post_id",
                        "identical": None,
                    }
                )
                continue

            stem = Path(post_id_raw.strip()).stem
            file_name = f"{stem}.json"
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "batch-angles-determinism",
                    "event": "post_start",
                    "post_id": stem,
                    "source_file": file_name,
                },
            )

            try:
                post = self.backend.get_post_local(file_name, step)
            except Exception as exc:
                row_results.append(
                    {
                        "post_id": stem,
                        "source_file": file_name,
                        "error": str(exc),
                        "identical": None,
                    }
                )
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "batch-angles-determinism",
                        "event": "post_error",
                        "post_id": stem,
                        "error": str(exc),
                    },
                )
                continue

            if hasattr(self.gen_angles, "build_dictionary_bundle_for_post"):
                dictionary_bundle = self.gen_angles.build_dictionary_bundle_for_post(post)
                dictionary = list(dictionary_bundle["texts"])
                dictionary_report = dict(dictionary_bundle["report"])
            else:
                dictionary = list(self.gen_angles.build_dictionary_for_post(post))
                dictionary_report = {
                    "dictionary_id": stable_hash(dictionary),
                    "texts_hash": stable_hash(dictionary),
                    "raw_entry_count": len(dictionary),
                    "source_counts": {},
                    "truncated_sources": [],
                    "capacity_applied": False,
                }
            input_hash = dictionary_report["texts_hash"]

            if not dictionary:
                row = {
                    "post_id": stem,
                    "source_file": file_name,
                    "input_text_blocks": 0,
                    "input_hash": input_hash,
                    "dictionary_id": dictionary_report["dictionary_id"],
                    "dictionary_raw_count": dictionary_report["raw_entry_count"],
                    "dictionary_source_counts": dictionary_report["source_counts"],
                    "dictionary_truncated_sources": dictionary_report["truncated_sources"],
                    "dictionary_capacity_applied": dictionary_report["capacity_applied"],
                    "error": "no text blocks for angles input",
                    "identical": None,
                }
                row_results.append(row)
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "batch-angles-determinism",
                        "event": "post_done",
                        **row,
                    },
                )
                continue

            try:
                raw_a = analyze_angles_from_texts(dictionary, use_cache=False)
                raw_b = analyze_angles_from_texts(dictionary, use_cache=False)
            except Exception as exc:
                row = {
                    "post_id": stem,
                    "source_file": file_name,
                    "input_text_blocks": len(dictionary),
                    "input_hash": input_hash,
                    "dictionary_id": dictionary_report["dictionary_id"],
                    "dictionary_raw_count": dictionary_report["raw_entry_count"],
                    "dictionary_source_counts": dictionary_report["source_counts"],
                    "dictionary_truncated_sources": dictionary_report["truncated_sources"],
                    "dictionary_capacity_applied": dictionary_report["capacity_applied"],
                    "error": str(exc),
                    "identical": None,
                }
                row_results.append(row)
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "batch-angles-determinism",
                        "event": "post_error",
                        "post_id": stem,
                        "error": str(exc),
                    },
                )
                continue

            norm_a = normalized_angles_from_raw(raw_a)
            norm_b = normalized_angles_from_raw(raw_b)
            h1 = stable_hash(norm_a)
            h2 = stable_hash(norm_b)
            identical = norm_a == norm_b

            row = {
                "post_id": stem,
                "source_file": file_name,
                "input_text_blocks": len(dictionary),
                "input_hash": input_hash,
                "dictionary_id": dictionary_report["dictionary_id"],
                "dictionary_raw_count": dictionary_report["raw_entry_count"],
                "dictionary_source_counts": dictionary_report["source_counts"],
                "dictionary_truncated_sources": dictionary_report["truncated_sources"],
                "dictionary_capacity_applied": dictionary_report["capacity_applied"],
                "run_1_count": len(norm_a),
                "run_2_count": len(norm_b),
                "run_1_hash": h1,
                "run_2_hash": h2,
                "identical": identical,
            }
            row_results.append(row)
            _LOG.info(
                "batch_angles_determinism post_id={} identical={} run_1_count={} run_2_count={}",
                stem,
                identical,
                len(norm_a),
                len(norm_b),
            )
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "batch-angles-determinism",
                    "event": "post_done",
                    "post_id": stem,
                    "identical": identical,
                    "run_1_hash": h1,
                    "run_2_hash": h2,
                },
            )

        tested_ok = [r for r in row_results if r.get("error") is None]
        all_identical = bool(tested_ok) and all(r.get("identical") is True for r in tested_ok)

        out = {
            "mode": "batch_angles_determinism",
            "step": step,
            "posts_requested": len(post_ids),
            "posts_succeeded": len(tested_ok),
            "all_identical": all_identical,
            "results": row_results,
        }
        self._emit(
            on_progress,
            "workflow_done",
            {
                "workflow": "batch-angles-determinism",
                "all_identical": all_identical,
                "posts_succeeded": len(tested_ok),
            },
        )
        return out

    def _full_pipeline_done(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        results: list[dict],
    ) -> list[dict]:
        """Emit the terminal ``workflow_done`` for a full-pipeline run and hand back its results."""
        self._emit(
            on_progress,
            "workflow_done",
            {"workflow": "full", "processed_count": len(results)},
        )
        return results

    def _full_pipeline_angle_researched(
        self,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        research_results: list[dict],
    ) -> list[dict]:
        """Explicit stage handoff: angle what was just researched, then finish the run."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "gen-angles", "source": "research", "count": len(research_results)},
        )
        final_results = self.gen_angles.process_post_objects(
            posts=research_results,
            step=ANGLES_STEP,
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "gen-angles", "processed_count": len(final_results)},
        )
        return self._full_pipeline_done(on_progress, final_results)

    def run_full_pipeline(
        self,
        start_step: str = DATA_LOAD_STEP,
        count: int = 1,
        payload: str | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[dict]:
        """
        Run full pipeline from start_step to final-step.

        Args:
            start_step: Starting step name
            count: Number of posts to process

        Returns:
            List of processed posts
        """
        results = []
        self._emit(
            on_progress,
            "workflow_start",
            {
                "workflow": "full",
                "start_step": start_step,
                "count": count,
                "payload_provided": bool(payload),
            },
        )

        if start_step == DATA_LOAD_STEP:
            data_results = self._call_with_optional_progress(
                self.run_data_load,
                on_progress,
                count=count,
            )
            if not data_results:
                return self._full_pipeline_done(on_progress, results)

            # Explicit stage handoff: research what we just loaded.
            self._emit(
                on_progress,
                "stage_start",
                {"stage": "research", "source": "data-load", "count": len(data_results)},
            )
            research_results = self.research.process_post_objects(
                posts=data_results,
                step=RESEARCH_STEP,
            )
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "research", "processed_count": len(research_results)},
            )
            if not research_results:
                return self._full_pipeline_done(on_progress, results)
            return self._full_pipeline_angle_researched(on_progress, research_results)

        if start_step == RESEARCH_STEP:
            research_results = self._call_with_optional_progress(
                self.run_research,
                on_progress,
                count=count,
            )
            if not research_results:
                return self._full_pipeline_done(on_progress, results)
            return self._full_pipeline_angle_researched(on_progress, research_results)

        if start_step == ANGLES_STEP:
            results = self._call_with_optional_progress(
                self.run_gen_angles,
                on_progress,
                count=count,
            )
            return self._full_pipeline_done(on_progress, results)

        raise ValueError(f"Unsupported start_step: {start_step}")
