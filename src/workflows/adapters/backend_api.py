"""Adapter for backend API endpoints."""
import json
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException

from workflows.config import get_config


class BackendAPIAdapter:
    """Adapter for backend API calls."""
    
    def __init__(self, base_url: Optional[str] = None):
        self.config = get_config()
        self.base_url = base_url or self.config.base_url
    
    def posts_list(
        self,
        step: str,
        count: int = 1,
        offset: int = 0,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get list of post filenames for a step."""
        from services.posts_service import list_posts

        return list_posts(count=count, step=step, tag=tag, offset=offset)
    
    def get_post(self, post_filename: str, step: str) -> Dict[str, Any]:
        """Get a single post by filename."""
        from services.posts_service import get_post

        return get_post(post=post_filename, step=step)
    
    def save_post(self, post: Dict[str, Any], step: str) -> Dict[str, Any]:
        """Save a post to the step's destination directory."""
        from services.posts_service import save_post

        return save_post(post_data=post, step=step)
    
    def save_object(
        self, data: Dict[str, Any], step: str, filename: str
    ) -> Dict[str, Any]:
        """Save an object to the step's destination directory."""
        from services.posts_service import save_object

        return save_object(data=data, step=step, filename=filename)
    
    def google_search(
        self,
        query: str,
        first: int = 1,
        count: int = 10,
    ) -> Dict[str, Any]:
        """Perform Google search."""
        from services.search_service import search_google

        return search_google(query=query, first=first, count=count)
    
    def semantic_search(
        self,
        text: str,
        objects: List[Dict[str, Any]],
        n: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Perform semantic search."""
        from services.semantic_service import semantic_search

        return semantic_search(query_text=text, objects_list=objects, n=n)
    
    def needle_finder_batch(
        self,
        needles: List[str],
        haystack: List[str],
    ) -> Dict[str, Any]:
        """Batch needle finder."""
        payload = {
            "needles": needles,
            "haystack": haystack,
        }
        try:
            response = requests.post(
                f"{self.base_url}/needle_finder_batch", json=payload, timeout=60
            )
            response.raise_for_status()
            return response.json()
        except RequestException:
            return self._needle_finder_batch_local(needles=needles, haystack=haystack)
    
    def analyze_angles(self, texts: List[str]) -> Dict[str, Any]:
        """Analyze angles from texts."""
        from services.angles_service import analyze_angles

        return {"results": analyze_angles(texts)}
    
    # Direct filesystem methods (for local execution without HTTP)
    def get_post_local(self, post_filename: str, step: str) -> Dict[str, Any]:
        """Get post directly from filesystem."""
        src_dir, _ = self.config.get_step_dirs(step)
        file_path = src_dir / post_filename
        if not file_path.exists():
            raise FileNotFoundError(f"Post file not found: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def save_post_local(self, post: Dict[str, Any], step: str) -> None:
        """Save post directly to filesystem."""
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must include 'id' field")
        
        _, dest_dir = self.config.get_step_dirs(step)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{post_id}.json"
        
        with open(dest_file, "w", encoding="utf-8") as f:
            json.dump(post, f, indent=2, ensure_ascii=False)
    
    def save_object_local(
        self, data: Dict[str, Any], step: str, filename: str
    ) -> None:
        """Save object directly to filesystem."""
        _, dest_dir = self.config.get_step_dirs(step)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / filename
        
        with open(dest_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _needle_finder_batch_local(
        self,
        needles: List[Any],
        haystack: List[str],
    ) -> Dict[str, Any]:
        """
        Local fallback for needle matching when backend HTTP is unavailable.
        """
        from services.semantic_service import find_best_match

        results: List[Dict[str, Any]] = []
        for needle in needles:
            try:
                if not isinstance(needle, str):
                    raise ValueError("must be a string")
                results.append(find_best_match(needle, haystack))
            except ValueError as exc:
                results.append(
                    {"error": f"Failed to process needle '{needle}': {str(exc)}"}
                )
            except Exception as exc:
                results.append(
                    {
                        "error": (
                            f"Unexpected error processing needle '{needle}': {str(exc)}"
                        )
                    }
                )

        return {"results": results}
