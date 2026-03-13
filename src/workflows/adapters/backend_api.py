"""Adapter for backend API endpoints."""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

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
        params = {
            "count": count,
            "step": step,
            "offset": offset,
        }
        if tag:
            params["tag"] = tag
        
        response = requests.get(f"{self.base_url}/posts_list", params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def get_post(self, post_filename: str, step: str) -> Dict[str, Any]:
        """Get a single post by filename."""
        params = {
            "post": post_filename,
            "step": step,
        }
        response = requests.get(f"{self.base_url}/get_post", params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def save_post(self, post: Dict[str, Any], step: str) -> Dict[str, Any]:
        """Save a post to the step's destination directory."""
        params = {"step": step}
        response = requests.post(
            f"{self.base_url}/save_post",
            params=params,
            json=post,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    
    def save_object(
        self, data: Dict[str, Any], step: str, filename: str
    ) -> Dict[str, Any]:
        """Save an object to the step's destination directory."""
        params = {"step": step, "filename": filename}
        response = requests.post(
            f"{self.base_url}/save_object",
            params=params,
            json=data,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    
    def google_search(
        self,
        query: str,
        first: int = 1,
        count: int = 10,
    ) -> Dict[str, Any]:
        """Perform Google search."""
        params = {
            "query": query,
            "first": first,
            "count": count,
        }
        response = requests.get(
            f"{self.base_url}/google_search", params=params, timeout=60
        )
        response.raise_for_status()
        return response.json()
    
    def semantic_search(
        self,
        text: str,
        objects: List[Dict[str, Any]],
        n: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Perform semantic search."""
        payload: Dict[str, Any] = {
            "text": text,
            "objects": objects,
        }
        if n is not None:
            payload["n"] = n
        
        response = requests.post(
            f"{self.base_url}/semantic_search", json=payload, timeout=60
        )
        response.raise_for_status()
        return response.json()
    
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
        response = requests.post(
            f"{self.base_url}/needle_finder_batch", json=payload, timeout=60
        )
        response.raise_for_status()
        return response.json()
    
    def analyze_angles(self, texts: List[str]) -> Dict[str, Any]:
        """Analyze angles from texts."""
        payload = {"texts": texts}
        response = requests.post(
            f"{self.base_url}/angles/analyze", json=payload, timeout=300
        )
        response.raise_for_status()
        return response.json()
    
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
