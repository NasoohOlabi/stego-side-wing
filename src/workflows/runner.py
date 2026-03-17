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
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run Stego pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {
                "stage": "stego",
                "post_id": post_id,
                "tag": tag,
                "list_offset": list_offset,
            },
        )
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
