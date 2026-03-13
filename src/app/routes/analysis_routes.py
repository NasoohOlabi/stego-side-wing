"""Analysis and URL fetching routes."""
from flask import Blueprint, jsonify

from app.schemas.validators import get_json_body, get_query_param
from infrastructure.config import STEPS
from services.analysis_service import fetch_url_content, fetch_url_content_crawl4ai, process_post_file

bp = Blueprint("analysis", __name__)


@bp.route("/process_file", methods=["POST"])
def process_file_endpoint():
    """API endpoint to process a file using the process_file function from ai_analyze."""
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    if "name" not in data:
        return jsonify({"error": "Missing 'name' in request body"}), 400
    if "step" not in data:
        return jsonify({"error": "Missing 'step' in request body"}), 400
    
    filename = data["name"]
    step = data["step"]
    if not isinstance(filename, str):
        return jsonify({"error": "'name' must be a string"}), 400
    if not isinstance(step, str):
        return jsonify({"error": "'step' must be a string"}), 400
    
    if step not in STEPS:
        return jsonify({"error": f"Invalid step: {step}"}), 400
    
    try:
        result = process_post_file(filename, step)
        return jsonify(result)
    except (FileNotFoundError, ValueError) as e:
        status = 404 if isinstance(e, FileNotFoundError) else 400
        return jsonify({"error": f"Error processing file: {str(e)}"}), status
    except Exception as e:
        return jsonify({"error": f"Error processing file: {str(e)}"}), 500


@bp.route("/fetch_url_content", methods=["POST"])
def fetch_url_content_route():
    """Fetch URL content using WebAnalyzer."""
    url, _ = get_query_param("url", str, required=False)
    normalized_url = (url or "").strip()
    
    try:
        result = fetch_url_content(normalized_url)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/fetch_url_content_crawl4ai", methods=["POST"])
def fetch_url_content_crawl4ai_route():
    """Fetch URL content using crawl4ai."""
    url, _ = get_query_param("url", str, required=False)
    normalized_url = (url or "").strip()
    
    try:
        result = fetch_url_content_crawl4ai(normalized_url)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
