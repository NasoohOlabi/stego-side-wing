"""Ports: what ``workflows`` needs from the outside, expressed without importing it.

``docs/development/architecture-layers.md`` allows ``services -> workflows`` but not the
reverse. Workflow code nevertheless needs post storage, search, semantic matching and
angle analysis, all of which are use cases owned by ``services``.

These Protocols state the requirement in the ``workflows`` layer. Implementations live in
``services`` and satisfy them structurally -- ``services`` does not import this module, and
this module does not import ``services``, so the dependency runs the documented direction.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LocalBackendPort(Protocol):
    """In-process backend operations a workflow can perform.

    Implemented by ``services.workflow_backend_client.LocalBackendClient``.
    """

    def posts_list(
        self, step: str, count: int = 1, offset: int = 0, tag: str | None = None
    ) -> dict[str, Any]: ...

    def get_post(self, post_filename: str, step: str) -> dict[str, Any]: ...

    def save_post(self, post: dict[str, Any], step: str) -> dict[str, Any]: ...

    def save_object(self, data: Any, step: str, filename: str) -> dict[str, Any]: ...

    def google_search(self, query: str, first: int = 1, count: int = 10) -> dict[str, Any]: ...

    def semantic_search(
        self, text: str, objects: list[dict[str, Any]], n: int | None = None
    ) -> dict[str, Any]: ...

    def analyze_angles(
        self,
        texts: list[str],
        *,
        use_cache: bool = True,
        max_results: int | None = None,
    ) -> dict[str, Any]: ...

    def get_post_local(self, post_filename: str, step: str) -> dict[str, Any]: ...

    def save_post_local(self, post: dict[str, Any], step: str) -> None: ...

    def save_object_local(self, data: Any, step: str, filename: str) -> None: ...

    def needle_finder_batch(self, needles: list[Any], haystack: list[str]) -> dict[str, Any]: ...


@runtime_checkable
class RemoteBackendPort(Protocol):
    """Backend operations served over HTTP by a running API process."""

    def needle_finder_batch(self, needles: list[str], haystack: list[str]) -> dict[str, Any]: ...
