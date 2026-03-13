"""Angles analysis routes."""
from flask import Blueprint, jsonify

from app.schemas.validators import get_json_body
from services.angles_service import analyze_angles

bp = Blueprint("angles", __name__)


@bp.route("/angles/analyze", methods=["POST"])
def analyze_angles_endpoint():
    """Proxy endpoint that runs the angles analysis script."""
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    texts = data.get("texts")
    if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
        return jsonify({"error": "'texts' must be a list of strings"}), 400
    
    try:
        results = analyze_angles(texts)
        return jsonify({"results": results}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        if "RequestException" in str(type(e).__name__):
            return jsonify({"error": f"LM Studio request failed: {e}"}), 502
        return jsonify({"error": f"Angles analysis failed: {e}"}), 500
