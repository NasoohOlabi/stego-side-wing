"""Angles analysis service."""
import logging
from typing import Any

from content_acquisition.angles.angle_runner import analyze_angles_from_texts

logger = logging.getLogger(__name__)


def analyze_angles(texts: object, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """
    Analyze angles from text chunks.
    
    Args:
        texts: List of text strings to analyze
        use_cache: When False, skip angles disk cache read/write (forces fresh LLM work).

    Returns:
        List of angle dicts with source_quote, tangent, category, and source_document
        (0-based index into ``texts``, counting only non-empty blocks in order).
        
    Raises:
        ValueError: If texts is invalid
        requests.RequestException: If the legacy LM Studio HTTP path fails (``WORKFLOW_LLM_BACKEND=lm_studio``)
    """
    if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
        raise ValueError("'texts' must be a list of strings")

    if not texts:
        raise ValueError("Provide at least one text block")

    cast_texts: list[str] = list(texts)
    try:
        logger.info(
            "analyze_angles",
            extra={
                "event": "angles",
                "action": "analyze",
                "text_blocks": len(cast_texts),
                "use_cache": use_cache,
            },
        )
        results = analyze_angles_from_texts(cast_texts, use_cache=use_cache)
        return results
    except ValueError:
        logger.exception(
            "angles analysis validation failed",
            extra={
                "event": "angles",
                "action": "analyze",
                "text_blocks": len(cast_texts),
                "use_cache": use_cache,
            },
        )
        raise
    except Exception:
        logger.exception(
            "angles analysis failed",
            extra={
                "event": "angles",
                "action": "analyze",
                "text_blocks": len(cast_texts),
                "use_cache": use_cache,
            },
        )
        raise
