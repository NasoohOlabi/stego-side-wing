"""Posts management routes."""
from flask import Blueprint, jsonify

from app.schemas.validators import get_json_body, get_query_param
from infrastructure.config import STEPS
from services.posts_service import get_post, list_posts, save_object, save_post

bp = Blueprint("posts", __name__)


@bp.route("/posts_list", methods=["GET"])
def posts_list():
    """API endpoint to fetch and return a list of posts."""
    count, err = get_query_param("count", int, required=True)
    if err:
        return err
    assert count is not None
    
    step, err = get_query_param("step", str, required=True)
    if err:
        return err
    assert step is not None
    
    tag, _ = get_query_param("tag", str, required=False)
    offset, _ = get_query_param("offset", int, required=False, default=0)
    assert offset is not None
    
    if step not in STEPS:
        return jsonify({"error": f"Invalid step: {step}"}), 400
    
    try:
        result = list_posts(count, step, tag, offset)
        return jsonify(result), 200
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/get_post", methods=["GET"])
def get_post_route():
    """API endpoint to fetch and return a single post."""
    post, err = get_query_param("post", str, required=True)
    if err:
        return err
    assert post is not None
    
    step, err = get_query_param("step", str, required=True)
    if err:
        return err
    assert step is not None
    
    if step not in STEPS:
        return jsonify({"error": f"Invalid step: {step}"}), 400
    
    try:
        result = get_post(post, step)
        return jsonify(result), 200
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 404 if isinstance(e, FileNotFoundError) else 400


@bp.route("/save_post", methods=["POST"])
def save_post_route():
    """Saves a post JSON to the step's dest_dir."""
    step, err = get_query_param("step", str, required=True)
    if err:
        return err
    assert step is not None
    
    if step not in STEPS:
        return jsonify({"error": f"Invalid step: {step}"}), 400
    
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    try:
        result = save_post(data, step)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/save_object", methods=["POST"])
def save_object_route():
    """Saves the request JSON body as-is to step's dest_dir with provided filename."""
    step, err = get_query_param("step", str, required=True)
    if err:
        return err
    assert step is not None
    
    if step not in STEPS:
        return jsonify({"error": f"Invalid step: {step}"}), 400
    
    filename, err = get_query_param("filename", str, required=True)
    if err:
        return err
    assert filename is not None
    
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    try:
        result = save_object(data, step, filename)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/save-json", methods=["POST"])
def save_json():
    """Accepts JSON body and saves it to ./output-results/{timestamp}.json"""
    import datetime
    from pathlib import Path
    
    data, err = get_json_body()
    if err:
        return err
    assert data is not None
    
    try:
        # Create output directory if it doesn't exist
        output_dir = Path("./output-results")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate ISO timestamp and make it filesystem-safe
        timestamp = datetime.datetime.now().isoformat()
        safe_timestamp = timestamp.replace(":", "-")

        # Construct file path
        filepath = output_dir / f"{safe_timestamp}.json"

        # Write JSON to file with pretty formatting
        import json
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)

        return jsonify(
            {
                "success": True,
                "message": "JSON saved successfully",
                "filename": filepath.name,
                "path": str(filepath),
            }
        ), 200
    except Exception as e:
        return jsonify({"error": f"Failed to save JSON: {str(e)}"}), 500
