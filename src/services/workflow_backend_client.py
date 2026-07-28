"""In-process implementation of the workflow backend port.

This is the adapter that composes services into the surface workflow code expects. It
lives in ``services`` rather than ``workflows`` because it is built *out of* services:
keeping it here means those imports are intra-layer, instead of the
``workflows -> services`` edge that ``docs/development/architecture-layers.md`` forbids.

It satisfies ``workflows.ports.LocalBackendPort`` structurally and does not import it.

The service imports below are function-local on purpose. Resolving them per call is what
lets tests monkeypatch dotted paths such as ``services.semantic_service.find_best_match``;
binding them at module import would freeze the original functions and silently ignore the
patch.
"""

from __future__ import annotations

import json
from typing import Any

from workflows.config import WorkflowConfig


class LocalBackendClient:
    """In-process client for services/filesystem-backed operations."""

    def __init__(self, config: WorkflowConfig):
        self.config = config

    def posts_list(
        self,
        step: str,
        count: int = 1,
        offset: int = 0,
        tag: str | None = None,
    ) -> dict[str, Any]:
        from services.posts_service import list_posts

        return list_posts(count=count, step=step, tag=tag, offset=offset)

    def get_post(self, post_filename: str, step: str) -> dict[str, Any]:
        from services.posts_service import get_post

        return get_post(post=post_filename, step=step)

    def save_post(self, post: dict[str, Any], step: str) -> dict[str, Any]:
        from services.posts_service import save_post

        return save_post(post_data=post, step=step)

    def save_object(self, data: Any, step: str, filename: str) -> dict[str, Any]:
        from services.posts_service import save_object

        return save_object(data=data, step=step, filename=filename)

    def google_search(self, query: str, first: int = 1, count: int = 10) -> dict[str, Any]:
        from services.search_service import search_google

        return search_google(query=query, first=first, count=count)

    def semantic_search(
        self, text: str, objects: list[dict[str, Any]], n: int | None = None
    ) -> dict[str, Any]:
        from services.semantic_service import semantic_search

        return semantic_search(query_text=text, objects_list=objects, n=n)

    def analyze_angles(
        self,
        texts: list[str],
        *,
        use_cache: bool = True,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        from services.angles_service import analyze_angles

        return {
            "results": analyze_angles(
                texts,
                use_cache=use_cache,
                max_results=max_results,
            )
        }

    def get_post_local(self, post_filename: str, step: str) -> dict[str, Any]:
        src_dir, _ = self.config.get_step_dirs(step)
        file_path = src_dir / post_filename
        if not file_path.exists():
            raise FileNotFoundError(f"Post file not found: {file_path}")
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)

    def save_post_local(self, post: dict[str, Any], step: str) -> None:
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must include 'id' field")
        _, dest_dir = self.config.get_step_dirs(step)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with open(dest_dir / f"{post_id}.json", "w", encoding="utf-8") as f:
            json.dump(post, f, indent=2, ensure_ascii=False)

    def save_object_local(self, data: Any, step: str, filename: str) -> None:
        _, dest_dir = self.config.get_step_dirs(step)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with open(dest_dir / filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def needle_finder_batch(self, needles: list[Any], haystack: list[str]) -> dict[str, Any]:
        from services.semantic_service import find_best_match

        results: list[dict[str, Any]] = []
        for needle in needles:
            try:
                if not isinstance(needle, str):
                    raise ValueError("must be a string")
                results.append(find_best_match(needle, haystack))
            except ValueError as exc:
                results.append({"error": f"Failed to process needle '{needle}': {exc!s}"})
            except Exception as exc:
                results.append({"error": f"Unexpected error processing needle '{needle}': {exc!s}"})
        return {"results": results}
