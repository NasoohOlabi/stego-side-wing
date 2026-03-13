"""Angles analysis service."""
from pipelines.angles.angle_runner import analyze_angles_from_texts


def analyze_angles(texts: object) -> list[dict[str, str]]:
    """
    Analyze angles from text chunks.
    
    Args:
        texts: List of text strings to analyze
        
    Returns:
        List of angle dicts with source_quote, tangent, category
        
    Raises:
        ValueError: If texts is invalid
        requests.RequestException: If LM Studio request fails
    """
    if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
        raise ValueError("'texts' must be a list of strings")

    if not texts:
        raise ValueError("Provide at least one text block")

    try:
        cast_texts = [x for x in texts if isinstance(x, str)]
        results = analyze_angles_from_texts(cast_texts)
        return results
    except ValueError as e:
        raise ValueError(str(e))
    except Exception as e:
        # Re-raise with context
        raise RuntimeError(f"Angles analysis failed: {e}") from e
