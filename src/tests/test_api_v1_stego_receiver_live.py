"""API tests for stego-receiver live simulation endpoint."""

import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_stego_receiver_live_requires_sender(client):
    r = client.post("/api/v1/workflows/stego-receiver-live", json={})
    assert r.status_code == 400


def test_stego_receiver_live_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes.runner,
        "run_stego_receiver_live_sim",
        lambda sender_user_id, **kwargs: {
            "succeeded": True,
            "stego": {"succeeded": True},
            "receiver": {"payload": "recovered"},
            "simulation": {"root": "/tmp"},
        },
    )

    r = client.post(
        "/api/v1/workflows/stego-receiver-live",
        json={"sender_user_id": "alice", "stream": False},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["receiver"]["payload"] == "recovered"
