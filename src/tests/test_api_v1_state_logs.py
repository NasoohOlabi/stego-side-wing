"""API routes for API JSONL log size and truncate."""
from __future__ import annotations

import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_state_logs_get_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "get_api_log_file_stats",
        lambda: {
            "file_logging_enabled": True,
            "path": "/tmp/example.jsonl",
            "bytes": 120,
        },
    )
    response = client.get("/api/v1/state/logs")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["bytes"] == 120
    assert payload["data"]["path"] == "/tmp/example.jsonl"


def test_state_logs_delete_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "get_api_log_file_stats",
        lambda: {"file_logging_enabled": True, "path": "/x", "bytes": 99},
    )
    monkeypatch.setattr(
        api_v1_routes,
        "clear_api_log_file",
        lambda: {"cleared": True, "path": "/x"},
    )
    response = client.delete("/api/v1/state/logs")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["cleared"] is True


def test_state_logs_delete_when_disabled(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "get_api_log_file_stats",
        lambda: {"file_logging_enabled": False, "path": None, "bytes": 0},
    )
    response = client.delete("/api/v1/state/logs")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_state_logs_delete_failure(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "get_api_log_file_stats",
        lambda: {"file_logging_enabled": True, "path": "/x", "bytes": 1},
    )
    monkeypatch.setattr(
        api_v1_routes,
        "clear_api_log_file",
        lambda: {"cleared": False, "path": "/x", "reason": "truncate_failed"},
    )
    response = client.delete("/api/v1/state/logs")
    assert response.status_code == 500
