"""Coercion helpers for the loosely-typed mappings that come out of JSON artifacts."""

from collections.abc import Mapping
from typing import Any


def dict_field(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return ``mapping[key]`` when it is a dict, else an empty dict.

    Replaces the ``x.get(k) if isinstance(x.get(k), dict) else {}`` idiom. That form calls
    ``get`` twice and, because the ``isinstance`` tests a different expression from the one
    whose value is used, it narrows nothing -- so every downstream ``.get`` on the result is
    an access on a possibly-``None`` value.
    """
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def list_field(mapping: Mapping[str, Any], key: str) -> list[Any]:
    """Return ``mapping[key]`` when it is a list, else an empty list.

    The list-shaped counterpart to :func:`dict_field`, with the same rationale.
    """
    value = mapping.get(key)
    return value if isinstance(value, list) else []
