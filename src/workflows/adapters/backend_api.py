"""Workflow backend adapter with explicit local/HTTP clients."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException

from workflows.config import WorkflowConfig, get_config


class LocalBackendClient:
    """In-process client for services/filesystem-backed operations."""

    def __init__(self, config: WorkflowConfig):
        self.config = config

    def posts_list(
        self,
        step: str,
        count: int = 1,
        offset: int = 0,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        from services.posts_service import list_posts

        return list_posts(count=count, step=step, tag=tag, offset=offset)

    def get_post(self, post_filename: str, step: str) -> Dict[str, Any]:
        from services.posts_service import get_post

        return get_post(post=post_filename, step=step)

    def save_post(self, post: Dict[str, Any], step: str) -> Dict[str, Any]:
        from services.posts_service import save_post

        return save_post(post_data=post, step=step)

    def save_object(self, data: Dict[str, Any], step: str, filename: str) -> Dict[str, Any]:
        from services.posts_service import save_object

        return save_object(data=data, step=step, filename=filename)

    def google_search(self, query: str, first: int = 1, count: int = 10) -> Dict[str, Any]:
        from services.search_service import search_google

        return search_google(query=query, first=first, count=count)

    def semantic_search(
        self, text: str, objects: List[Dict[str, Any]], n: Optional[int] = None
    ) -> Dict[str, Any]:
        from services.semantic_service import semantic_search

        return semantic_search(query_text=text, objects_list=objects, n=n)

    def analyze_angles(self, texts: List[str], *, use_cache: bool = True) -> Dict[str, Any]:
        from services.angles_service import analyze_angles

        return {"results": analyze_angles(texts, use_cache=use_cache)}

    def get_post_local(self, post_filename: str, step: str) -> Dict[str, Any]:
        src_dir, _ = self.config.get_step_dirs(step)
        file_path = src_dir / post_filename
        if not file_path.exists():
            raise FileNotFoundError(f"Post file not found: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_post_local(self, post: Dict[str, Any], step: str) -> None:
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must include 'id' field")
        _, dest_dir = self.config.get_step_dirs(step)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with open(dest_dir / f"{post_id}.json", "w", encoding="utf-8") as f:
            json.dump(post, f, indent=2, ensure_ascii=False)

    def save_object_local(self, data: Dict[str, Any], step: str, filename: str) -> None:
        _, dest_dir = self.config.get_step_dirs(step)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with open(dest_dir / filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def needle_finder_batch(self, needles: List[Any], haystack: List[str]) -> Dict[str, Any]:
        from services.semantic_service import find_best_match

        results: List[Dict[str, Any]] = []
        for needle in needles:
            try:
                if not isinstance(needle, str):
                    raise ValueError("must be a string")
                results.append(find_best_match(needle, haystack))
            except ValueError as exc:
                results.append({"error": f"Failed to process needle '{needle}': {str(exc)}"})
            except Exception as exc:
                results.append(
                    {"error": f"Unexpected error processing needle '{needle}': {str(exc)}"}
                )
        return {"results": results}


class HttpBackendClient:
    """HTTP client for remote backend API operations."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def needle_finder_batch(self, needles: List[str], haystack: List[str]) -> Dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/needle_finder_batch",
            json={"needles": needles, "haystack": haystack},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()


class BackendAPIAdapter:
    """Facade that exposes one interface with explicit local/HTTP behavior."""

    def __init__(self, base_url: Optional[str] = None):
        self.config = get_config()
        self.base_url = base_url or self.config.base_url
        self.local = LocalBackendClient(self.config)
        self.http = HttpBackendClient(self.base_url)

    def _local_client(self) -> LocalBackendClient:
        """Backward-compatible lazy local client for __new__-constructed tests."""
        if not hasattr(self, "local"):
            if not hasattr(self, "config"):
                self.config = get_config()
            self.local = LocalBackendClient(self.config)
        return self.local

    def posts_list(
        self,
        step: str,
        count: int = 1,
        offset: int = 0,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._local_client().posts_list(step=step, count=count, offset=offset, tag=tag)

    def get_post(self, post_filename: str, step: str) -> Dict[str, Any]:
        return self._local_client().get_post(post_filename=post_filename, step=step)

    def save_post(self, post: Dict[str, Any], step: str) -> Dict[str, Any]:
        return self._local_client().save_post(post=post, step=step)

    def save_object(self, data: Dict[str, Any], step: str, filename: str) -> Dict[str, Any]:
        return self._local_client().save_object(data=data, step=step, filename=filename)

    def google_search(self, query: str, first: int = 1, count: int = 10) -> Dict[str, Any]:
        return self._local_client().google_search(query=query, first=first, count=count)

    def semantic_search(
        self,
        text: str,
        objects: List[Dict[str, Any]],
        n: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._local_client().semantic_search(text=text, objects=objects, n=n)

    def needle_finder_batch(self, needles: List[str], haystack: List[str]) -> Dict[str, Any]:
        try:
            return self.http.needle_finder_batch(needles=needles, haystack=haystack)
        except RequestException:
            return self._needle_finder_batch_local(needles=needles, haystack=haystack)

    def analyze_angles(self, texts: List[str], *, use_cache: bool = True) -> Dict[str, Any]:
        return self._local_client().analyze_angles(texts, use_cache=use_cache)

    def get_post_local(self, post_filename: str, step: str) -> Dict[str, Any]:
        return self._local_client().get_post_local(post_filename=post_filename, step=step)

    def save_post_local(self, post: Dict[str, Any], step: str) -> None:
        self._local_client().save_post_local(post=post, step=step)

    def save_object_local(self, data: Dict[str, Any], step: str, filename: str) -> None:
        self._local_client().save_object_local(data=data, step=step, filename=filename)

    def _needle_finder_batch_local(self, needles: List[Any], haystack: List[str]) -> Dict[str, Any]:
        """Backward-compatible local batch matcher."""
        return self._local_client().needle_finder_batch(needles=needles, haystack=haystack)
