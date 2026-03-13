"""Request validation helpers."""

from typing import Any, TypeVar

from flask import Response, jsonify, request

JSONDict = dict[str, Any]
ErrorResponse = tuple[Response, int]
T = TypeVar("T")


def get_json_body() -> tuple[JSONDict | None, ErrorResponse | None]:
    """
    Get and validate JSON request body.

    Returns:
        Tuple of (data, error_response). If error_response is not None, return it.
        Otherwise, data contains the parsed JSON dict.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "Invalid or missing JSON body"}), 400)
    return data, None


def get_query_param(
    key: str,
    param_type: type[T] = str,
    required: bool = False,
    default: T | None = None,
) -> tuple[T | None, ErrorResponse | None]:
    """
    Get and validate query parameter.

    Args:
        key: Parameter name
        param_type: Type to convert to (int, str, etc.)
        required: Whether parameter is required
        default: Default value if not required and not present

    Returns:
        Tuple of (value, error_response). If error_response is not None, return it.
    """
    value = request.args.get(key, type=param_type)
    if value is None:
        if required:
            return None, (jsonify({"error": f"Missing required query parameter: {key}"}), 400)
        return default, None
    return value, None
