import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_validate_post_requires_post_id(client):
    response = client.post("/api/v1/workflows/validate-post", json={"stream": False})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False


def test_validate_post_sync_success(client, monkeypatch):
    from app.routes import api_v1_routes

    expected = {
        "post_id": "abc",
        "valid": True,
        "steps": {
            "data_load": {"matches": True, "changed_keys": []},
            "research": {"matches": True, "changed_keys": []},
            "gen_angles": {"matches": True, "changed_keys": []},
        },
    }

    monkeypatch.setattr(
        api_v1_routes.runner,
        "validate_post_pipeline",
        lambda post_id, on_progress=None, **kwargs: expected,
    )

    response = client.post(
        "/api/v1/workflows/validate-post",
        json={"post_id": "abc", "stream": False},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["valid"] is True
    assert payload["data"]["post_id"] == "abc"


def test_validate_post_streaming(client, monkeypatch):
    from app.routes import api_v1_routes

    def _run(post_id, on_progress=None, **kwargs):
        if on_progress:
            on_progress("stage_progress", {"stage": "validate-post", "post_id": post_id})
        return {"post_id": post_id, "valid": True, "steps": {}}

    monkeypatch.setattr(api_v1_routes.runner, "validate_post_pipeline", _run)

    response = client.post(
        "/api/v1/workflows/validate-post",
        json={"post_id": "abc", "stream": True},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "event: status" in body
    assert "event: result" in body
    assert "event: done" in body
    assert "event: heartbeat" not in body
