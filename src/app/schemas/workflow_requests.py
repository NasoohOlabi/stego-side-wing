"""Pydantic request shapes for workflow API bodies (incremental typing boundary)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DecodeWorkflowRequest(BaseModel):
    """POST /workflows/decode JSON body."""

    model_config = ConfigDict(extra="ignore")

    stego_text: str
    angles: list[Any]
    few_shots: list[Any] | None = None

    @field_validator("angles")
    @classmethod
    def angles_must_be_list(cls, v: object) -> object:
        if not isinstance(v, list):
            raise ValueError("angles must be a list")
        return v

    @field_validator("few_shots")
    @classmethod
    def few_shots_list_or_none(cls, v: object) -> object:
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError("few_shots must be a list when provided")
        return v


class ReceiverWorkflowRequest(BaseModel):
    """Shared receiver options for POST /workflows/receiver and related paths."""

    model_config = ConfigDict(extra="ignore")

    post: dict[str, Any]
    sender_user_id: str = Field(min_length=1)
    compressed_bitstring: str | None = None
    allow_fallback: bool = False
    use_fetch_cache: bool = True
    use_terms_cache: bool = True
    persist_terms_cache: bool = True
    use_fetch_cache_research: bool = True
    max_padding_bits: int = Field(default=256, ge=0)

    @field_validator("sender_user_id", mode="before")
    @classmethod
    def strip_sender(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v


class ValidatePostWorkflowRequest(BaseModel):
    """POST /workflows/validate-post JSON body."""

    model_config = ConfigDict(extra="ignore")

    post_id: str = Field(min_length=1)

    @field_validator("post_id", mode="before")
    @classmethod
    def strip_post_id(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v
    use_terms_cache: bool = False
    persist_terms_cache: bool = False
    use_fetch_cache: bool = False
    allow_angles_fallback: bool = False
