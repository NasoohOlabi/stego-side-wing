import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_prep_until_google_quota_then_stego_requires_tag(client):
    response = client.post(
        "/api/v1/workflows/prep-until-google-quota-then-stego",
        json={"stream": False},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False


def test_prep_until_google_quota_then_stego_sync_success(client, monkeypatch):
    from app.routes import api_v1_routes

    expected = {
        "succeeded": True,
        "tag": "version_42",
        "prep": {"stop_reason": "google_search_quota_detected"},
        "stego": {"processed_count": 2},
        "phase_transition": {"reason": "google_search_quota_detected"},
    }

    monkeypatch.setattr(
        api_v1_routes.runner,
        "run_prep_until_google_quota_then_stego",
        lambda **kwargs: expected,
    )

    response = client.post(
        "/api/v1/workflows/prep-until-google-quota-then-stego",
        json={"tag": "version_42", "stream": False},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["tag"] == "version_42"
    assert payload["data"]["stego"]["processed_count"] == 2


def test_prep_until_google_quota_then_stego_sync_no_quota_no_stego(client, monkeypatch):
    from app.routes import api_v1_routes

    expected = {
        "succeeded": True,
        "tag": "version_42",
        "prep": {"stop_reason": "no_more_posts", "quota_detected": False},
        "stego": {"processed_count": 0, "stopped_reason": "not_started_quota_not_detected"},
        "phase_transition": None,
    }

    monkeypatch.setattr(
        api_v1_routes.runner,
        "run_prep_until_google_quota_then_stego",
        lambda **kwargs: expected,
    )

    response = client.post(
        "/api/v1/workflows/prep-until-google-quota-then-stego",
        json={"tag": "version_42", "stream": False},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["phase_transition"] is None
    assert payload["data"]["stego"]["stopped_reason"] == "not_started_quota_not_detected"


def test_prep_until_google_quota_then_stego_streaming(client, monkeypatch):
    from app.routes import api_v1_routes

    def _run(**kwargs):
        on_progress = kwargs.get("on_progress")
        if on_progress:
            on_progress(
                "workflow_start",
                {"workflow": "prep-until-google-quota-then-stego", "tag": "version_42"},
            )
            on_progress("phase_start", {"phase": "prep"})
            on_progress(
                "quota_detected",
                {"phase": "prep", "stage": "research", "message": "quota"},
            )
            on_progress(
                "phase_transition",
                {
                    "from_phase": "prep",
                    "to_phase": "stego",
                    "reason": "google_search_quota_detected",
                },
            )
            on_progress("phase_start", {"phase": "stego"})
            on_progress(
                "stego_post_done",
                {"phase": "stego", "post_id": "p1", "succeeded": True, "retry_count": 0},
            )
        return {"succeeded": True, "tag": "version_42"}

    monkeypatch.setattr(api_v1_routes.runner, "run_prep_until_google_quota_then_stego", _run)

    response = client.post(
        "/api/v1/workflows/prep-until-google-quota-then-stego",
        json={"tag": "version_42", "stream": True},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "event: status" in body
    assert "event: result" in body
    assert "event: done" in body
    assert '"event": "quota_detected"' in body
    assert '"event": "phase_transition"' in body
    assert '"event": "stego_post_done"' in body
    assert "heartbeat" not in body
