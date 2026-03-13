"""Generate angles from post content."""
from typing import Any, Dict, List

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.contracts import AngleResult
from workflows.utils.text_utils import (
    build_post_text_dictionary,
    flatten_comments,
    parse_json_array_response,
)


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
    
    def generate_angles(self, post: Dict) -> List[Dict[str, Any]]:
        """
        Generate angles from post content.
        
        Args:
            post: Post dictionary with content, search_results, comments
        
        Returns:
            List of angle dictionaries
        """
        # Build dictionary of texts
        dictionary = self._build_dictionary(post)
        
        if not dictionary:
            return []
        
        # Use backend API for angle analysis (leverages existing angle_runner)
        try:
            response = self.backend.analyze_angles(dictionary)
            results = response.get("results", [])
            
            # Convert to angle format
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
            
            return angles
            
        except Exception as e:
            print(f"Error generating angles via API: {e}")
            # Fallback: use LLM directly
            return self._generate_angles_llm(dictionary)
    
    def _generate_angles_llm(self, texts: List[str]) -> List[Dict[str, Any]]:
        """Generate angles using LLM directly."""
        # Combine texts
        combined_text = "\n\n---\n\n".join(texts[:10])  # Limit to first 10 texts
        
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
            print(f"Error generating angles with LLM: {e}")
            return []
    
    def process_post(
        self,
        post: Dict,
        step: str = "angles-step",
    ) -> Dict:
        """
        Process a post to generate angles.
        
        Args:
            post: Post dictionary
            step: Workflow step name
        
        Returns:
            Post dictionary with angles added
        """
        # Generate angles
        angles = self.generate_angles(post)
        
        # Update post
        post["angles"] = angles
        post["options_count"] = len(angles)
        
        return post
    
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
                print(f"Error loading post {file_name}: {e}")
        return self.process_post_objects(posts=posts, step=step)

    def process_post_objects(
        self,
        posts: List[Dict[str, Any]],
        step: str = "angles-step",
    ) -> List[Dict[str, Any]]:
        """Process already-loaded post objects and persist angle-enriched versions."""
        processed_posts: List[Dict[str, Any]] = []
        for post in posts:
            post_id = post.get("id", "<unknown>")
            try:
                processed = self.process_post(post, step)
                self.backend.save_post_local(processed, step=step)
                processed_posts.append(processed)
            except Exception as e:
                print(f"Error processing post {post_id}: {e}")
        return processed_posts
