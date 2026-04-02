import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_double_process_new_post_sync_success(client, monkeypatch):
    from app.routes import api_v1_routes

    expected = {
        "post_id": "abc123",
        "source_file": "abc123.json",
        "passes": {
            "pass_1_cached": {"settings": {"use_fetch_cache": True}},
            "pass_2_validation": {"settings": {"use_fetch_cache": True}},
        },
    }

    monkeypatch.setattr(
        api_v1_routes.runner,
        "run_double_process_new_post",
        lambda on_progress=None, allow_angles_fallback=False: expected,
    )

    response = client.post(
        "/api/v1/workflows/double-process-new-post",
        json={"stream": False},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["post_id"] == "abc123"


def test_double_process_new_post_streaming(client, monkeypatch):
    from app.routes import api_v1_routes

    def _run(on_progress=None, allow_angles_fallback=False):
        if on_progress:
            on_progress("stage_progress", {"stage": "double-process-new-post"})
        return {"post_id": "abc123"}

    monkeypatch.setattr(api_v1_routes.runner, "run_double_process_new_post", _run)

    response = client.post(
        "/api/v1/workflows/double-process-new-post",
        json={"stream": True},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "event: status" in body
    assert "event: result" in body
    assert "event: done" in body
