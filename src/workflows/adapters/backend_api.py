"""Workflow backend adapter with explicit local/HTTP clients."""

from __future__ import annotations

from typing import Any

import requests
from requests.exceptions import RequestException

from workflows.config import WorkflowConfig, get_config
from workflows.ports import LocalBackendPort, RemoteBackendPort


def _build_default_local_client(config: WorkflowConfig) -> LocalBackendPort:
    """Construct the in-process backend implementation.

    This is the single place ``workflows`` reaches into ``services``, and it is deferred to
    call time so importing this module does not pull the service layer in. Everything else
    goes through ``LocalBackendPort``; pass ``local=`` to supply your own implementation.

    ``config`` is passed in rather than resolved here: callers may hold a config that is not
    the current ``get_config()`` (tests attach their own to a ``__new__``-built adapter).
    """
    from services.workflow_backend_client import LocalBackendClient

    return LocalBackendClient(config)


class HttpBackendClient:
    """HTTP client for remote backend API operations."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def needle_finder_batch(self, needles: list[str], haystack: list[str]) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/needle_finder_batch",
            json={"needles": needles, "haystack": haystack},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()


class BackendAPIAdapter:
    """Facade that exposes one interface with explicit local/HTTP behavior."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        local: LocalBackendPort | None = None,
        http: RemoteBackendPort | None = None,
    ):
        self.config = get_config()
        resolved = base_url or self.config.base_url or "http://127.0.0.1:5001"
        self.base_url: str = resolved
        self.local: LocalBackendPort = local or _build_default_local_client(self.config)
        self.http: RemoteBackendPort = http or HttpBackendClient(self.base_url)

    def _local_client(self) -> LocalBackendPort:
        """Backward-compatible lazy local client for __new__-constructed tests."""
        if not hasattr(self, "local"):
            if not hasattr(self, "config"):
                self.config = get_config()
            self.local = _build_default_local_client(self.config)
        return self.local

    def posts_list(
        self,
        step: str,
        count: int = 1,
        offset: int = 0,
        tag: str | None = None,
    ) -> dict[str, Any]:
        return self._local_client().posts_list(step=step, count=count, offset=offset, tag=tag)

    def get_post(self, post_filename: str, step: str) -> dict[str, Any]:
        return self._local_client().get_post(post_filename=post_filename, step=step)

    def save_post(self, post: dict[str, Any], step: str) -> dict[str, Any]:
        return self._local_client().save_post(post=post, step=step)

    def save_object(self, data: Any, step: str, filename: str) -> dict[str, Any]:
        return self._local_client().save_object(data=data, step=step, filename=filename)

    def google_search(self, query: str, first: int = 1, count: int = 10) -> dict[str, Any]:
        return self._local_client().google_search(query=query, first=first, count=count)

    def semantic_search(
        self,
        text: str,
        objects: list[dict[str, Any]],
        n: int | None = None,
    ) -> dict[str, Any]:
        return self._local_client().semantic_search(text=text, objects=objects, n=n)

    def needle_finder_batch(self, needles: list[str], haystack: list[str]) -> dict[str, Any]:
        try:
            return self.http.needle_finder_batch(needles=needles, haystack=haystack)
        except RequestException:
            return self._needle_finder_batch_local(needles=needles, haystack=haystack)

    def analyze_angles(self, texts: list[str], *, use_cache: bool = True) -> dict[str, Any]:
        return self._local_client().analyze_angles(texts, use_cache=use_cache)

    def get_post_local(self, post_filename: str, step: str) -> dict[str, Any]:
        return self._local_client().get_post_local(post_filename=post_filename, step=step)

    def save_post_local(self, post: dict[str, Any], step: str) -> None:
        self._local_client().save_post_local(post=post, step=step)

    def save_object_local(self, data: Any, step: str, filename: str) -> None:
        self._local_client().save_object_local(data=data, step=step, filename=filename)

    def _needle_finder_batch_local(self, needles: list[Any], haystack: list[str]) -> dict[str, Any]:
        """Backward-compatible local batch matcher."""
        return self._local_client().needle_finder_batch(needles=needles, haystack=haystack)
