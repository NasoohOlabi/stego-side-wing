"""API route for recent git updates summary."""

from __future__ import annotations

import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_recent_updates_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "get_recent_git_updates",
        lambda days, limit: {
            "days": days,
            "limit": limit,
            "count": 1,
            "authors": ["nasooh"],
            "top_paths": [{"path": "src/API.py", "touches": 1}],
            "commits": [],
            "generated_at_utc": "2026-04-26T00:00:00+00:00",
        },
    )
    response = client.get("/api/v1/state/recent-updates?days=5&limit=3")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["days"] == 5
    assert payload["data"]["limit"] == 3


def test_recent_updates_git_failure(client, monkeypatch):
    from app.routes import api_v1_routes

    def _raise(*_args, **_kwargs):
        raise RuntimeError("git failed")

    monkeypatch.setattr(api_v1_routes, "get_recent_git_updates", _raise)
    response = client.get("/api/v1/state/recent-updates")
    assert response.status_code == 500
    assert response.get_json()["ok"] is False


def test_recent_updates_rejects_invalid_days(client):
    response = client.get("/api/v1/state/recent-updates?days=abc")
    assert response.status_code == 400
    assert response.get_json()["ok"] is False
