"""Generate angles from post content."""
import logging
from typing import Any, Dict, List

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.text_utils import (
    build_post_text_dictionary,
    flatten_comments,
    parse_json_array_response,
)
from workflows.utils.workflow_llm_prompts import get_prompts
from pipelines.angles.angle_runner import angles_model_name
from pipelines.angles.angle_runner import SYSTEM_PROMPT as ANGLES_SYSTEM_PROMPT
from pipelines.angles.angle_runner import TEMPERATURE as ANGLES_TEMPERATURE
from pipelines.angles.angle_runner import USER_PROMPT_TEMPLATE as ANGLES_USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


class GenAnglesPipeline:
    """Pipeline for generating angles from posts."""
    
    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.llm = LLMAdapter()
        self.config = get_config()
        self._last_batch_summary: Dict[str, Any] = {}
    
    def _flatten_comments(self, comments: List[Dict]) -> List[Dict]:
        """Flatten nested comment structure."""
        return flatten_comments(comments)
    
    def _build_dictionary(self, post: Dict) -> List[str]:
        """Build dictionary of texts from post."""
        return build_post_text_dictionary(post)

    def build_dictionary_for_post(self, post: Dict[str, Any]) -> List[str]:
        """Public alias for workflow runner / tools that need the same inputs as gen_angles."""
        return self._build_dictionary(post)

    def preview_post(
        self,
        post: Dict[str, Any],
        allow_fallback: bool = False,
    ) -> Dict[str, Any]:
        """Generate angles without mutating or saving artifacts."""
        post_id = str(post.get("id") or "<unknown>")
        dictionary = self._build_dictionary(post)
        report = {
            "post_id": post_id,
            "input_count": len(dictionary),
            "input_hash": stable_hash(dictionary),
            "input_items": [
                {
                    "index": idx,
                    "length": len(text),
                    "hash": stable_hash(text),
                    "preview": text_preview(text),
                }
                for idx, text in enumerate(dictionary)
            ],
            "provider": "lm_studio",
            "model": angles_model_name(),
            "temperature": ANGLES_TEMPERATURE,
            "system_prompt_hash": stable_hash(ANGLES_SYSTEM_PROMPT),
            "user_prompt_template_hash": stable_hash(ANGLES_USER_PROMPT_TEMPLATE),
            "used_fallback": False,
        }
        if not dictionary:
            report.update({"angles": [], "angles_hash": stable_hash([]), "options_count": 0})
            return {"post": dict(post, angles=[], options_count=0), "report": report}

        try:
            response = self.backend.analyze_angles(dictionary)
            results = response.get("results", [])
            angles = []
            for result in results:
                if isinstance(result, dict):
                    angle: Dict[str, Any] = {
                        "source_quote": result.get("source_quote", ""),
                        "tangent": result.get("tangent", ""),
                        "category": result.get("category", ""),
                    }
                    sd = result.get("source_document")
                    if isinstance(sd, int):
                        angle["source_document"] = sd
                    if angle["source_quote"] and angle["tangent"] and angle["category"]:
                        angles.append(angle)
            processed_post = dict(post)
            processed_post["angles"] = angles
            processed_post["options_count"] = len(angles)
            report.update(
                {
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                }
            )
            logger.info(
                "angles preview post_id=%s input_count=%s angles=%s hash=%s",
                post_id,
                len(dictionary),
                len(angles),
                report["angles_hash"],
            )
            return {"post": processed_post, "report": report}
        except Exception as e:
            if not allow_fallback:
                logger.exception(
                    "angles generation failed post_id=%s",
                    post_id,
                    extra={
                        "event": "gen_angles",
                        "post_id": post_id,
                        "input_count": len(dictionary),
                        "error_kind": type(e).__name__,
                    },
                )
                raise
            logger.exception("angles api failed; using fallback for post_id=%s", post_id)
            angles = self._generate_angles_llm(dictionary)
            for a in angles:
                a.setdefault("source_document", 0)
            processed_post = dict(post)
            processed_post["angles"] = angles
            processed_post["options_count"] = len(angles)
            report.update(
                {
                    "used_fallback": True,
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                    "fallback_error": str(e),
                }
            )
            return {"post": processed_post, "report": report}
    
    def generate_angles(self, post: Dict, allow_fallback: bool = False) -> List[Dict[str, Any]]:
        """
        Generate angles from post content.
        
        Args:
            post: Post dictionary with content, search_results, comments
        
        Returns:
            List of angle dictionaries
        """
        return list(self.preview_post(post, allow_fallback=allow_fallback)["report"]["angles"])
    
    def _generate_angles_llm(self, texts: List[str]) -> List[Dict[str, Any]]:
        """Generate angles using LLM directly."""
        # Combine texts
        combined_text = "\n\n---\n\n".join(texts)
        ga = get_prompts().gen_angles
        prompt = ga.user_template.format(combined_text=combined_text)
        system_message = ga.system_template

        try:
            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=self.config.model,
                provider="lm_studio",
                temperature=0.0,
            )
            
            parsed_items = parse_json_array_response(response)
            # Single combined prompt: no reliable per-chunk index; use 0 (see analyze_angles_from_texts).
            fallback_doc = 0
            return [
                {
                    "source_quote": a.get("source_quote", ""),
                    "tangent": a.get("tangent", ""),
                    "category": a.get("category", ""),
                    "source_document": fallback_doc,
                }
                for a in parsed_items
                if isinstance(a, dict)
            ]
            
        except Exception:
            logger.exception("angles fallback llm failed")
            return []
    
    def process_post(
        self,
        post: Dict,
        step: str = "angles-step",
        allow_fallback: bool = False,
    ) -> Dict:
        """
        Process a post to generate angles.
        
        Args:
            post: Post dictionary
            step: Workflow step name
        
        Returns:
            Post dictionary with angles added
        """
        return self.preview_post(post, allow_fallback=allow_fallback)["post"]
    
    def process_posts(
        self,
        step: str = "angles-step",
        count: int = 1,
        offset: int = 0,
    ) -> List[Dict]:
        """
        Process multiple posts to generate angles.
        
        Args:
            step: Workflow step name
            count: Number of posts to process
            offset: Offset for pagination
        
        Returns:
            List of posts with angles added
        """
        # Get list of post filenames
        posts_list = self.backend.posts_list(step=step, count=count, offset=offset)
        file_names = posts_list.get("fileNames", [])
        load_failed_count = 0
        if not file_names:
            self._last_batch_summary = {
                "step": step,
                "requested_count": count,
                "listed_count": 0,
                "loaded_count": 0,
                "load_failed_count": 0,
                "processed_count": 0,
                "failed_count": 0,
            }
            return []

        posts: List[Dict[str, Any]] = []
        for file_name in file_names:
            try:
                posts.append(self.backend.get_post_local(file_name, step))
            except Exception:
                logger.exception("angles load failed file=%s", file_name)
                load_failed_count += 1
        processed_posts = self.process_post_objects(posts=posts, step=step)
        processing_summary = dict(getattr(self, "_last_batch_summary", {}))
        processing_failed = int(processing_summary.get("failed_count", 0) or 0)
        self._last_batch_summary = {
            "step": step,
            "requested_count": count,
            "listed_count": len(file_names),
            "loaded_count": len(posts),
            "load_failed_count": load_failed_count,
            "processed_count": len(processed_posts),
            "processing_failed_count": processing_failed,
            "failed_count": load_failed_count + processing_failed,
            "allow_fallback": bool(processing_summary.get("allow_fallback", False)),
        }
        if self._last_batch_summary["failed_count"] > 0:
            logger.warning(
                "angles batch summary step=%s requested=%s loaded=%s processed=%s failed=%s",
                step,
                self._last_batch_summary["requested_count"],
                self._last_batch_summary["loaded_count"],
                self._last_batch_summary["processed_count"],
                self._last_batch_summary["failed_count"],
                extra={"event": "angles_batch", **self._last_batch_summary},
            )
        return processed_posts

    def process_post_objects(
        self,
        posts: List[Dict[str, Any]],
        step: str = "angles-step",
        allow_fallback: bool = False,
    ) -> List[Dict[str, Any]]:
        """Process already-loaded post objects and persist angle-enriched versions."""
        processed_posts: List[Dict[str, Any]] = []
        for post in posts:
            post_id = post.get("id", "<unknown>")
            try:
                processed = self.process_post(post, step, allow_fallback=allow_fallback)
                self.backend.save_post_local(processed, step=step)
                processed_posts.append(processed)
            except Exception:
                logger.exception("angles process failed post_id=%s", post_id)
        self._last_batch_summary = {
            "step": step,
            "input_count": len(posts),
            "processed_count": len(processed_posts),
            "failed_count": len(posts) - len(processed_posts),
            "allow_fallback": allow_fallback,
        }
        return processed_posts

    def process_post_id(
        self,
        post_id: str,
        step: str = "angles-step",
        allow_fallback: bool = False,
    ) -> Dict[str, Any]:
        """
        Process one post by ID and persist angle output.

        Args:
            post_id: Post identifier without `.json`
            step: Workflow step name

        Returns:
            Processed post dictionary with angles
        """
        file_name = f"{post_id}.json"
        post = self.backend.get_post_local(file_name, step)
        results = self.process_post_objects(
            posts=[post],
            step=step,
            allow_fallback=allow_fallback,
        )
        if not results:
            raise RuntimeError(f"GenAngles returned no result for post {post_id}")
        return results[0]
