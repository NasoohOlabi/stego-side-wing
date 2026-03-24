import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_batch_angles_determinism_sync_success(client, monkeypatch):
    from app.routes import api_v1_routes

    expected = {
        "mode": "batch_angles_determinism",
        "posts_requested": 1,
        "posts_succeeded": 1,
        "all_identical": True,
        "results": [{"post_id": "abc", "identical": True}],
    }

    monkeypatch.setattr(
        api_v1_routes.runner,
        "run_batch_angles_determinism",
        lambda post_ids, step="angles-step", on_progress=None: expected,
    )

    response = client.post(
        "/api/v1/workflows/batch-angles-determinism",
        json={"stream": False, "post_ids": ["abc"]},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["posts_requested"] == 1


def test_batch_angles_determinism_streaming(client, monkeypatch):
    from app.routes import api_v1_routes

    def _run(post_ids, step="angles-step", on_progress=None):
        if on_progress:
            on_progress("stage_progress", {"stage": "batch-angles-determinism"})
        return {"mode": "batch_angles_determinism", "results": []}

    monkeypatch.setattr(api_v1_routes.runner, "run_batch_angles_determinism", _run)

    response = client.post(
        "/api/v1/workflows/batch-angles-determinism",
        json={"stream": True, "post_ids": ["x"]},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "event: status" in body
    assert "event: result" in body
    assert "event: done" in body


def test_batch_angles_determinism_requires_post_ids(client):
    response = client.post(
        "/api/v1/workflows/batch-angles-determinism",
        json={"stream": False, "post_ids": []},
    )
    assert response.status_code == 400
