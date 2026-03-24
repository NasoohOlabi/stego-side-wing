"""Generate angles from post content."""
import logging
from typing import Any, Dict, List

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.contracts import AngleResult
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.text_utils import (
    build_post_text_dictionary,
    flatten_comments,
    parse_json_array_response,
)
from pipelines.angles.angle_runner import MODEL_NAME as ANGLES_MODEL_NAME
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
    
    def _flatten_comments(self, comments: List[Dict]) -> List[Dict]:
        """Flatten nested comment structure."""
        return flatten_comments(comments)
    
    def _build_dictionary(self, post: Dict) -> List[str]:
        """Build dictionary of texts from post."""
        return build_post_text_dictionary(post)

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
            "model": ANGLES_MODEL_NAME,
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
                    angle = {
                        "source_quote": result.get("source_quote", ""),
                        "tangent": result.get("tangent", ""),
                        "category": result.get("category", ""),
                    }
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
                logger.exception("angles generation failed post_id=%s", post_id)
                raise RuntimeError(f"Angles generation failed for post {post_id}: {e}") from e
            logger.exception("angles api failed; using fallback for post_id=%s", post_id)
            angles = self._generate_angles_llm(dictionary)
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
        
        prompt = f"""I have a block of texts from any domain — it could be educational, technical, journalistic, creative, or conversational. I want you to extract phrases or quotes that could spark commentary, opinions, or deeper exploration. For each quote, generate a structured JSON object with:
- `"source_quote"`: A short phrase or sentence from the text that could inspire discussion.
- `"tangent"`: A brief description of the idea, opinion, or deeper topic I could explore based on that quote.
- `"category"`: A high-level theme that groups the tangent (e.g. "Politics", "Technology", "Education", "Philosophy", "Culture", "Business").

Please give me at least 15 items. Return ONLY a JSON array, no markdown fences, no explanations.

Texts:
{combined_text}"""
        
        system_message = """You are a specialized Texts Analysis and Structuring Agent. Your sole function is to process input blocks of texts and extract key discussion points, formatting the entire output as a single, valid JSON array of objects.

**CRITICAL OUTPUT DIRECTIVE:**
The entire output **MUST** be the raw JSON array beginning with `[` and ending with `]`. **DO NOT** include any markdown fences (like ```json or ```), explanations, preambles, or postambles.

**STRICT OUTPUT CONSTRAINTS:**
1. **Format:** Your entire response **MUST** be a single JSON array (`[...]`). Do not include any preceding or trailing text, explanations, code fences, or commentary.
2. **Minimum Count:** You **MUST** generate a minimum of 15 JSON objects in the array.
3. **Schema:** Each object **MUST** adhere strictly to the following schema with exactly these three keys:
   * `"source_quote"` (string): A short, compelling quote or phrase extracted directly from the input text.
   * `"tangent"` (string): A brief, provocative description of the deeper topic, opinion, or line of inquiry inspired by the quote.
   * `"category"` (string): A high-level thematic label (e.g., "Technology", "Philosophy", "Business", "Culture", "Science")."""
        
        try:
            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=self.config.model,
                provider="lm_studio",
                temperature=0.0,
            )
            
            parsed_items = parse_json_array_response(response)
            return [
                {
                    "source_quote": a.get("source_quote", ""),
                    "tangent": a.get("tangent", ""),
                    "category": a.get("category", ""),
                }
                for a in parsed_items
                if isinstance(a, dict)
            ]
            
        except Exception as e:
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
        
        if not file_names:
            return []
        
        posts: List[Dict[str, Any]] = []
        for file_name in file_names:
            try:
                posts.append(self.backend.get_post_local(file_name, step))
            except Exception as e:
                logger.exception("angles load failed file=%s", file_name)
        return self.process_post_objects(posts=posts, step=step)

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
            except Exception as e:
                logger.exception("angles process failed post_id=%s", post_id)
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
