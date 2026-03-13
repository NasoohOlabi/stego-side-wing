"""Semantic search routes."""
from flask import Blueprint, jsonify

from app.schemas.validators import get_json_body
from services.semantic_service import find_best_match, semantic_search

bp = Blueprint("semantic", __name__)


@bp.route("/semantic_search", methods=["POST"])
def semantic_search_route():
    """API endpoint for semantic similarity search."""
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    query_text = data.get("text")
    objects_list = data.get("objects")
    n = data.get("n")
    if not isinstance(query_text, str):
        return jsonify({"error": "Missing or invalid 'text' field (must be a string)"}), 400
    if not isinstance(objects_list, list):
        return jsonify({"error": "Missing or invalid 'objects' field (must be a list)"}), 400
    
    try:
        result = semantic_search(query_text, objects_list, n)
        return jsonify(result), 200
    except (ValueError, ImportError) as e:
        status = 400 if isinstance(e, ValueError) else 500
        return jsonify({"error": str(e)}), status
    except Exception as e:
        return jsonify({"error": f"Error processing semantic search: {str(e)}"}), 500


@bp.route("/needle_finder", methods=["POST"])
def needle_finder():
    """Find the best matching document from an array using semantic similarity."""
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    needle = data.get("needle")
    haystack = data.get("haystack")
    if not isinstance(needle, str):
        return jsonify({"error": "Missing or invalid 'needle' field (must be a string)"}), 400
    if not isinstance(haystack, list) or not all(isinstance(item, str) for item in haystack):
        return jsonify({"error": "Missing or invalid 'haystack' field (must be a list of strings)"}), 400
    
    try:
        result = find_best_match(needle, haystack)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ImportError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"Error processing needle finder: {str(e)}"}), 500


@bp.route("/needle_finder_batch", methods=["POST"])
def needle_finder_batch():
    """Find the best matching documents from an array using semantic similarity."""
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    needles = data.get("needles")
    haystack = data.get("haystack")
    
    if not needles or not isinstance(needles, list):
        return jsonify({"error": "Missing or invalid 'needles' field (must be a list)"}), 400
    
    if not haystack or not isinstance(haystack, list):
        return jsonify({"error": "Missing or invalid 'haystack' field (must be a list)"}), 400
    
    results: list[dict[str, str] | dict[str, object]] = []
    for needle in needles:
        if not isinstance(needle, str):
            results.append({"error": f"Failed to process needle '{needle}': must be a string"})
            continue
        try:
            result = find_best_match(needle, haystack)
            results.append(result)
        except ValueError as e:
            results.append({"error": f"Failed to process needle '{needle}': {str(e)}"})
        except Exception as e:
            results.append({"error": f"Unexpected error processing needle '{needle}': {str(e)}"})
    
    return jsonify({"results": results}), 200
