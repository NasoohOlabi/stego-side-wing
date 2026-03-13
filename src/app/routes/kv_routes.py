"""Key-value store routes."""
from flask import Blueprint, jsonify, request

from services.kv_service import get_value, init_db, migrate_json_to_sqlite, set_value

bp = Blueprint("kv", __name__)

# Initialize database on module load
migrate_json_to_sqlite()
init_db()


@bp.route("/set", methods=["POST"])
def set_value_route():
    """POST endpoint to set a key-value pair."""
    data = request.get_json(force=True, silent=True)
    
    if not data or "key" not in data or "value" not in data:
        return jsonify({"error": 'Missing "key" or "value" in request body'}), 400
    
    key = str(data["key"])
    value = data["value"]
    
    try:
        result = set_value(key, value)
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/get/<k>", methods=["GET"])
def get_value_route(k: str):
    """GET endpoint to retrieve a value by key."""
    try:
        result = get_value(k)
        if result:
            return jsonify(result), 200
        else:
            return jsonify({"error": f'Key "{k}" not found'}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
