from flask import Flask

from app.schemas.validators import get_json_body, get_query_param


def test_get_json_body_rejects_non_json():
    app = Flask(__name__)
    with app.test_request_context("/", method="POST", data="not-json"):
        data, err = get_json_body()
        assert data is None
        assert err is not None
        response, status = err
        assert status == 400
        assert response.get_json()["error"] == "Invalid or missing JSON body"


def test_get_json_body_accepts_dict_payload():
    app = Flask(__name__)
    with app.test_request_context("/", method="POST", json={"ok": True}):
        data, err = get_json_body()
        assert err is None
        assert data == {"ok": True}


def test_get_query_param_required_missing_returns_error():
    app = Flask(__name__)
    with app.test_request_context("/"):
        value, err = get_query_param("count", int, required=True)
        assert value is None
        assert err is not None
        response, status = err
        assert status == 400
        assert "Missing required query parameter: count" in response.get_json()["error"]


def test_get_query_param_optional_uses_default():
    app = Flask(__name__)
    with app.test_request_context("/"):
        value, err = get_query_param("offset", int, required=False, default=5)
        assert err is None
        assert value == 5


def test_get_query_param_casts_present_value():
    app = Flask(__name__)
    with app.test_request_context("/?count=7"):
        value, err = get_query_param("count", int, required=True)
        assert err is None
        assert value == 7
