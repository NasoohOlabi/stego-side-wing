"""Semantic search and similarity services."""
from typing import Any, Dict, List, Optional


# Global model instance for sentence transformers (lazy-loaded)
_semantic_model = None


def get_semantic_model():
    """Lazy-load the sentence transformer model."""
    global _semantic_model
    if _semantic_model is None:
        try:
            import torch
            from sentence_transformers import SentenceTransformer

            print("Loading sentence transformer model 'all-MiniLM-L6-v2'...")

            # Explicitly set device to avoid meta tensor issues
            device = "cuda" if torch.cuda.is_available() else "cpu"

            _semantic_model = SentenceTransformer(
                "all-MiniLM-L6-v2", device=device
            )

            # Verify model is on correct device and not meta
            try:
                _ = next(_semantic_model.parameters()).device
            except RuntimeError as e:
                if "meta" in str(e).lower():
                    print("⚠️  Detected meta device issue, reloading on CPU...")
                    _semantic_model = SentenceTransformer(
                        "all-MiniLM-L6-v2", device="cpu"
                    )

            print(f"✅ Model loaded successfully on device: {device}")
        except ImportError:
            raise ImportError(
                "sentence-transformers library not installed. Install it with: pip install sentence-transformers"
            )
    return _semantic_model


def semantic_search(
    query_text: str, objects_list: List[Dict[str, Any]], n: Optional[int] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Perform semantic similarity search.
    
    Args:
        query_text: Query text to search for
        objects_list: List of objects to search through
        n: Optional number of top results to return
        
    Returns:
        Dict with 'results' list containing objects with scores and ranks
        
    Raises:
        ValueError: If inputs are invalid
        ImportError: If required libraries are not available
    """
    if not query_text:
        raise ValueError("Missing 'text' field in request body")

    if not objects_list:
        raise ValueError("Missing or invalid 'objects' field (must be a list)")

    if len(objects_list) == 0:
        return {"results": []}

    # Validate and convert n to integer if provided
    if n is not None:
        try:
            n = int(n)
            if n < 1:
                raise ValueError("'n' must be a positive integer")
        except (ValueError, TypeError):
            raise ValueError("'n' must be a valid integer or None")

    # Load the model
    model = get_semantic_model()

    # Import util for cosine similarity
    from sentence_transformers import util

    # Prepare the data for matching
    doc_texts = []
    for obj in objects_list:
        # Build text representation from common fields
        text_parts = []

        # Try common field names
        for field in ["category", "source_quote", "tangent", "title", "summary", "content"]:
            if field in obj:
                text_parts.append(str(obj[field]))

        # If no common fields found, convert entire object to string
        if not text_parts:
            text_parts = [
                str(v) for v in obj.values() if isinstance(v, (str, int, float))
            ]

        doc_text = " ".join(text_parts) if text_parts else str(obj)
        doc_texts.append(doc_text)

    # Generate embeddings
    query_embedding = model.encode(query_text, convert_to_tensor=True)
    doc_embeddings = model.encode(doc_texts, convert_to_tensor=True)

    # Calculate cosine similarity
    cosine_scores = util.cos_sim(query_embedding, doc_embeddings)[0]

    # Get scores as list and pair with indices
    scores_with_indices = [
        (float(cosine_scores[i].item()), i) for i in range(len(objects_list))
    ]

    # Sort by score (descending)
    scores_with_indices.sort(reverse=True, key=lambda x: x[0])

    # Get top N results
    if n is not None:
        scores_with_indices = scores_with_indices[:n]

    # Build response
    results = []
    for rank, (score, idx) in enumerate(scores_with_indices, start=1):
        results.append(
            {
                "object": objects_list[idx],
                "score": round(score, 4),
                "rank": rank,
            }
        )

    return {"results": results}


def find_best_match(needle: object, haystack: object) -> Dict[str, Any]:
    """
    Find the best matching document using semantic similarity.
    
    Args:
        needle: The search text to find
        haystack: List of documents to search through
        
    Returns:
        Dict with best_match, index, and score
        
    Raises:
        ValueError: If inputs are invalid
        ImportError: If required libraries are not available
    """
    if not isinstance(needle, str) or not needle:
        raise ValueError("Missing or invalid 'needle' field (must be a string)")

    if not isinstance(haystack, list) or not haystack:
        raise ValueError("Missing or invalid 'haystack' field (must be a list)")

    # Convert all haystack items to strings
    haystack_strings = [str(doc) for doc in haystack]

    # Filter out empty strings
    if not any(haystack_strings):
        raise ValueError("All haystack items are empty")

    # Load the model
    model = get_semantic_model()

    # Import util for cosine similarity
    from sentence_transformers import util

    # Generate embeddings
    needle_embedding = model.encode(needle, convert_to_tensor=True)
    haystack_embeddings = model.encode(
        haystack_strings, convert_to_tensor=True
    )

    # Calculate cosine similarity
    cosine_scores = util.cos_sim(needle_embedding, haystack_embeddings)[0]

    # Find the best match (highest score)
    best_score = float(cosine_scores[0].item())
    best_index = 0

    for i in range(1, len(haystack_strings)):
        score = float(cosine_scores[i].item())
        if score > best_score:
            best_score = score
            best_index = i

    return {
        "best_match": haystack_strings[best_index],
        "index": best_index,
        "score": round(best_score, 4),
    }
