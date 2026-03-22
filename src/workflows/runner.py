"""Workflow runner for orchestrating pipeline execution."""
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.pipelines.data_load import DataLoadPipeline
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline
from workflows.pipelines.research import ResearchPipeline
from workflows.pipelines.stego import StegoPipeline
from workflows.utils.protocol_utils import stable_hash

logger = logging.getLogger(__name__)

class WorkflowRunner:
    """Main workflow runner for orchestrating pipelines."""
    
    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.data_load = DataLoadPipeline()
        self.research = ResearchPipeline()
        self.gen_angles = GenAnglesPipeline()
        self.stego = StegoPipeline()
        self.decode = DecodePipeline()
        self.gen_terms = GenSearchTermsPipeline()

    @staticmethod
    def _emit(
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
        event: str,
        payload: Dict[str, Any],
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
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
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
    def _load_json(path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _summarize_stage_payload(stage_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary = {"hash": stable_hash(payload)}
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
    ) -> Dict[str, Any]:
        return self.data_load.preview_post_id(
            post_id=post_id,
            step="filter-url-unresolved",
            use_cache=use_cache,
        )

    def preview_research_post(
        self,
        post_id: str,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        source_post: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        file_name = f"{post_id}.json"
        post = source_post or self.backend.get_post_local(file_name, "filter-researched")
        return self.research.preview_post(
            post=post,
            step="filter-researched",
            force=True,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )

    def preview_gen_angles_post(
        self,
        post_id: str,
        source_post: Optional[Dict[str, Any]] = None,
        allow_fallback: bool = False,
    ) -> Dict[str, Any]:
        file_name = f"{post_id}.json"
        post = source_post or self.backend.get_post_local(file_name, "angles-step")
        return self.gen_angles.preview_post(post=post, allow_fallback=allow_fallback)

    @classmethod
    def _collect_diff_paths(
        cls,
        left: Any,
        right: Any,
        prefix: str = "",
        limit: int = 50,
    ) -> List[str]:
        diffs: List[str] = []

        def walk(a: Any, b: Any, path: str) -> None:
            if len(diffs) >= limit:
                return
            if type(a) is not type(b):
                diffs.append(path or "$")
                return
            if isinstance(a, dict):
                a_keys = set(a.keys())
                b_keys = set(b.keys())
                for key in sorted(a_keys - b_keys):
                    if len(diffs) >= limit:
                        return
                    next_path = f"{path}.{key}" if path else key
                    diffs.append(next_path)
                for key in sorted(b_keys - a_keys):
                    if len(diffs) >= limit:
                        return
                    next_path = f"{path}.{key}" if path else key
                    diffs.append(next_path)
                for key in sorted(a_keys & b_keys):
                    next_path = f"{path}.{key}" if path else key
                    walk(a[key], b[key], next_path)
                return
            if isinstance(a, list):
                if len(a) != len(b):
                    diffs.append(path or "$")
                    return
                for idx, (a_item, b_item) in enumerate(zip(a, b)):
                    next_path = f"{path}[{idx}]" if path else f"[{idx}]"
                    walk(a_item, b_item, next_path)
                return
            if a != b:
                diffs.append(path or "$")

        walk(left, right, prefix)
        return diffs
    
    def run_data_load(
        self,
        count: int = 100,
        offset: int = 0,
        batch_size: int = 5,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict]:
        """Run DataLoad pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "data-load", "count": count, "offset": offset, "batch_size": batch_size},
        )
        results = self.data_load.process_posts(
            step="filter-url-unresolved",
            count=count,
            offset=offset,
            batch_size=batch_size,
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "data-load", "processed_count": len(results)},
        )
        return results
    
    def run_research(
        self,
        count: int = 1,
        offset: int = 0,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict]:
        """Run Research pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "research", "count": count, "offset": offset},
        )
        results = self.research.process_posts(
            step="filter-researched",
            count=count,
            offset=offset,
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "research", "processed_count": len(results)},
        )
        return results
    
    def run_gen_angles(
        self,
        count: int = 1,
        offset: int = 0,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict]:
        """Run GenAngles pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "gen-angles", "count": count, "offset": offset},
        )
        results = self.gen_angles.process_posts(
            step="angles-step",
            count=count,
            offset=offset,
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "gen-angles", "processed_count": len(results)},
        )
        return results
    
    def run_stego(
        self,
        post_id: Optional[str] = None,
        payload: Optional[str] = None,
        tag: Optional[str] = None,
        list_offset: int = 1,
        run_all: bool = False,
        max_posts: Optional[int] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run Stego pipeline."""
        max_posts_cap: Optional[int] = (
            max_posts if max_posts is not None and max_posts >= 1 else None
        )
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
            result = self.stego.process_post(
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=list_offset,
            )
            self._emit(
                on_progress,
                "stage_done",
                {
                    "stage": "stego",
                    "succeeded": bool(result.get("succeeded")),
                    "retry_count": int(result.get("retry_count", 0)),
                },
            )
            return result

        results: List[Dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        seen_failed_post_ids: set[str] = set()
        stop_reason = "no_unprocessed_posts"

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
                if "No unprocessed posts found" in str(exc):
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
            },
        )
        return result
    
    def run_decode(
        self,
        stego_text: str,
        angles: List[Dict[str, Any]],
        few_shots: Optional[List[Dict[str, Any]]] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Optional[int]:
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
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "decode", "decoded_index": decoded_idx},
        )
        return decoded_idx
    
    def run_gen_search_terms(
        self,
        post_id: str,
        post_title: Optional[str] = None,
        post_text: Optional[str] = None,
        post_url: Optional[str] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[str]:
        """Run GenSearchTerms pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "gen-terms", "post_id": post_id},
        )
        terms = self.gen_terms.generate(
            post_id=post_id,
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "gen-terms", "terms_count": len(terms)},
        )
        return terms

    def validate_post_pipeline(
        self,
        post_id: str,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        use_terms_cache: bool = False,
        persist_terms_cache: bool = False,
        use_fetch_cache: bool = False,
        allow_angles_fallback: bool = False,
    ) -> Dict[str, Any]:
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

        stage_steps = {
            "data_load": "filter-url-unresolved",
            "research": "filter-researched",
            "gen_angles": "angles-step",
        }

        baseline: Dict[str, Dict[str, Any]] = {}
        for stage_name, step in stage_steps.items():
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
        stage_errors: Dict[str, str] = {}
        rerun_payloads: Dict[str, Dict[str, Any]] = {}
        protocol_reports: Dict[str, Dict[str, Any]] = {}

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

        steps_report: Dict[str, Dict[str, Any]] = {}
        valid = True
        upstream_failed = False
        for stage_name, step in stage_steps.items():
            if upstream_failed:
                steps_report[stage_name] = {
                    "step": step,
                    "comparison": "skipped",
                    "matches": None,
                    "changed_keys": [],
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
            changed_keys = [] if matches else self._collect_diff_paths(
                baseline_payload, rerun_payload
            )
            steps_report[stage_name] = {
                "step": step,
                "comparison": "match" if matches else "mismatch",
                "matches": matches,
                "changed_keys": changed_keys,
                "comparison_note": (
                    "Saved artifact and live rerun are byte-for-byte equal."
                    if matches
                    else (
                        "Mismatch: live rerun produced different JSON than the saved workflow artifact "
                        "for this stage (see changed_keys). This indicates protocol or data drift, not "
                        "a failed rerun."
                    )
                ),
                "baseline_summary": self._summarize_stage_payload(stage_name, baseline_payload),
                "rerun_summary": self._summarize_stage_payload(stage_name, rerun_payload),
                "protocol_report": protocol_reports.get(stage_name),
            }
            valid = valid and matches

        outcome: str
        if valid:
            outcome = "protocol_match"
            validation_explanation = (
                "All three stages completed and each live rerun matched its saved artifact."
            )
        elif any(
            steps_report[s].get("comparison") == "mismatch" for s in stage_steps
        ):
            outcome = "protocol_mismatch"
            validation_explanation = (
                "At least one stage finished rerunning but the live payload differed from the saved "
                "artifact. That is a true baseline-vs-rerun mismatch (see comparison / changed_keys on "
                "those stages)."
            )
        else:
            outcome = "rerun_incomplete"
            validation_explanation = (
                "A stage failed during rerun or was skipped, so validation could not establish whether "
                "the protocol still matches baselines. This is not labeled as a protocol mismatch; "
                "fix the failing stage and retry."
            )

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
        logger.info(
            "validate_post post_id=%s valid=%s use_terms_cache=%s use_fetch_cache=%s",
            post_id,
            valid,
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
    
    def run_full_pipeline(
        self,
        start_step: str = "filter-url-unresolved",
        count: int = 1,
        payload: Optional[str] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict]:
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
        
        if start_step == "filter-url-unresolved":
            data_results = self._call_with_optional_progress(
                self.run_data_load,
                on_progress,
                count=count,
            )
            if not data_results:
                self._emit(
                    on_progress,
                    "workflow_done",
                    {"workflow": "full", "processed_count": 0},
                )
                return results

            # Explicit stage handoff: research what we just loaded.
            self._emit(
                on_progress,
                "stage_start",
                {"stage": "research", "source": "data-load", "count": len(data_results)},
            )
            research_results = self.research.process_post_objects(
                posts=data_results,
                step="filter-researched",
            )
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "research", "processed_count": len(research_results)},
            )
            if not research_results:
                self._emit(
                    on_progress,
                    "workflow_done",
                    {"workflow": "full", "processed_count": 0},
                )
                return results

            # Explicit stage handoff: angle what we just researched.
            self._emit(
                on_progress,
                "stage_start",
                {"stage": "gen-angles", "source": "research", "count": len(research_results)},
            )
            final_results = self.gen_angles.process_post_objects(
                posts=research_results,
                step="angles-step",
            )
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "gen-angles", "processed_count": len(final_results)},
            )
            self._emit(
                on_progress,
                "workflow_done",
                {"workflow": "full", "processed_count": len(final_results)},
            )
            return final_results

        if start_step == "filter-researched":
            research_results = self._call_with_optional_progress(
                self.run_research,
                on_progress,
                count=count,
            )
            if not research_results:
                self._emit(
                    on_progress,
                    "workflow_done",
                    {"workflow": "full", "processed_count": 0},
                )
                return results
            self._emit(
                on_progress,
                "stage_start",
                {"stage": "gen-angles", "source": "research", "count": len(research_results)},
            )
            final_results = self.gen_angles.process_post_objects(
                posts=research_results,
                step="angles-step",
            )
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "gen-angles", "processed_count": len(final_results)},
            )
            self._emit(
                on_progress,
                "workflow_done",
                {"workflow": "full", "processed_count": len(final_results)},
            )
            return final_results

        if start_step == "angles-step":
            results = self._call_with_optional_progress(
                self.run_gen_angles,
                on_progress,
                count=count,
            )
            self._emit(
                on_progress,
                "workflow_done",
                {"workflow": "full", "processed_count": len(results)},
            )
            return results

        raise ValueError(f"Unsupported start_step: {start_step}")
