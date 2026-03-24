import json

import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_receiver_requires_post_and_sender(client):
    r = client.post("/api/v1/workflows/receiver", json={"stream": False})
    assert r.status_code == 400


def test_receiver_sync_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes.runner,
        "run_receiver",
        lambda post, sender_user_id, **kwargs: {
            "succeeded": True,
            "payload": "recovered",
            "post_id": post.get("id"),
        },
    )

    r = client.post(
        "/api/v1/workflows/receiver",
        json={
            "post": {"id": "p1", "comments": []},
            "sender_user_id": "alice",
            "stream": False,
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["payload"] == "recovered"


def test_receiver_stream_has_run_id_and_trace(client, monkeypatch):
    from app.routes import api_v1_routes

    def _run(post, sender_user_id, on_progress=None, **kwargs):
        if on_progress:
            on_progress("receiver.locate_comment", {"post_id": "p1"})
        return {"succeeded": True, "payload": "x"}

    monkeypatch.setattr(api_v1_routes.runner, "run_receiver", _run)

    r = client.post(
        "/api/v1/workflows/receiver",
        json={
            "post": {"id": "p1", "comments": []},
            "sender_user_id": "alice",
            "stream": True,
        },
    )
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert "run_id" in text
    for line in text.split("\n"):
        if line.startswith("data: ") and "accepted" in line:
            data = json.loads(line[6:])
            assert "run_id" in data
            break
    else:
        pytest.fail("no accepted event with run_id")
