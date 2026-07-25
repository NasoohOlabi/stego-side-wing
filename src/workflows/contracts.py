"""Typed payloads passed between workflow stages."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FetchUrlResult(BaseModel):
    """Outcome of fetching and extracting one URL.

    Frozen: stages pass this along and branch on it, but nothing mutates an instance --
    ``FetchUrlContentPipeline`` builds a new one per attempt rather than editing in place.
    """

    model_config = ConfigDict(frozen=True)

    url: str
    success: bool
    text: str | None = None
    content_type: str | None = None
    error: str | None = None
