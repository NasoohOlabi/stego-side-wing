"""Workflow runner for orchestrating pipeline execution."""
from typing import Any, Callable, Dict, List, Optional

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.pipelines.data_load import DataLoadPipeline
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline
from workflows.pipelines.research import ResearchPipeline
from workflows.pipelines.stego import StegoPipeline


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
        if max_posts is not None and max_posts < 1:
            raise ValueError("'max_posts' must be >= 1 when provided")
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
                "max_posts": max_posts,
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
            if max_posts is not None and len(results) >= max_posts:
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
            "max_posts": max_posts,
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
    
    def run_full_pipeline(
        self,
        start_step: str = "filter-url-unresolved",
        count: int = 1,
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
            {"workflow": "full", "start_step": start_step, "count": count},
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
