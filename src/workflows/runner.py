"""Workflow runner for orchestrating pipeline execution."""
from typing import Any, Dict, List, Optional

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
    
    def run_data_load(
        self,
        count: int = 100,
        offset: int = 0,
        batch_size: int = 5,
    ) -> List[Dict]:
        """Run DataLoad pipeline."""
        return self.data_load.process_posts(
            step="filter-url-unresolved",
            count=count,
            offset=offset,
            batch_size=batch_size,
        )
    
    def run_research(
        self,
        count: int = 1,
        offset: int = 0,
    ) -> List[Dict]:
        """Run Research pipeline."""
        return self.research.process_posts(
            step="filter-researched",
            count=count,
            offset=offset,
        )
    
    def run_gen_angles(
        self,
        count: int = 1,
        offset: int = 0,
    ) -> List[Dict]:
        """Run GenAngles pipeline."""
        return self.gen_angles.process_posts(
            step="angles-step",
            count=count,
            offset=offset,
        )
    
    def run_stego(
        self,
        post_id: str,
        payload: str,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run Stego pipeline."""
        return self.stego.process_post(
            post_id=post_id,
            payload=payload,
            tag=tag,
        )
    
    def run_decode(
        self,
        stego_text: str,
        angles: List[Dict[str, Any]],
        few_shots: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[int]:
        """Run Decode pipeline."""
        return self.decode.decode(
            stego_text=stego_text,
            angles=angles,
            few_shots=few_shots,
        )
    
    def run_gen_search_terms(
        self,
        post_id: str,
        post_title: Optional[str] = None,
        post_text: Optional[str] = None,
        post_url: Optional[str] = None,
    ) -> List[str]:
        """Run GenSearchTerms pipeline."""
        return self.gen_terms.generate(
            post_id=post_id,
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
        )
    
    def run_full_pipeline(
        self,
        start_step: str = "filter-url-unresolved",
        count: int = 1,
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
        
        # Step 1: DataLoad (if starting from unresolved)
        if start_step == "filter-url-unresolved":
            data_results = self.run_data_load(count=count)
            if not data_results:
                return results
        
        # Step 2: Research
        research_results = self.run_research(count=count)
        if not research_results:
            return results
        
        # Step 3: GenAngles
        angles_results = self.run_gen_angles(count=count)
        if not angles_results:
            return results
        
        results = angles_results
        return results
