import json

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
        lambda on_progress=None, allow_angles_fallback=False, explicit_post_id=None: expected,
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

    def _run(on_progress=None, allow_angles_fallback=False, explicit_post_id=None):
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


def test_double_process_new_post_passes_explicit_post_id(client, monkeypatch):
    from app.routes import api_v1_routes
    from app.routes.api_v1 import routes_workflows

    monkeypatch.setattr(routes_workflows, "try_read_double_process_claim", lambda: None)

    captured: list[str | None] = []

    def _run(on_progress=None, allow_angles_fallback=False, explicit_post_id=None):
        captured.append(explicit_post_id)
        return {
            "post_id": explicit_post_id or "x",
            "source_file": f"{explicit_post_id or 'x'}.json",
        }

    monkeypatch.setattr(api_v1_routes.runner, "run_double_process_new_post", _run)

    response = client.post(
        "/api/v1/workflows/double-process-new-post",
        json={"stream": False, "post_id": "my_post"},
    )
    assert response.status_code == 200
    assert captured == ["my_post"]


def test_double_process_new_post_claim_conflict_returns_400(client, monkeypatch):
    from app.routes import api_v1_routes
    from app.routes.api_v1 import routes_workflows

    monkeypatch.setattr(
        routes_workflows,
        "has_active_run_for_command",
        lambda _cmd: True,
    )
    monkeypatch.setattr(
        routes_workflows,
        "try_read_double_process_claim",
        lambda: ("other_id", "other_id.json"),
    )

    def _should_not_run(**kwargs):
        raise AssertionError("runner should not run when claim conflicts")

    monkeypatch.setattr(api_v1_routes.runner, "run_double_process_new_post", _should_not_run)

    response = client.post(
        "/api/v1/workflows/double-process-new-post",
        json={"stream": False, "post_id": "mine"},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["details"]["claim_post_id"] == "other_id"
    assert payload["details"]["requested_post_id"] == "mine"


def test_double_process_new_post_stale_claim_cleared_when_idle(client, monkeypatch):
    from app.routes import api_v1_routes
    from app.routes.api_v1 import routes_workflows
    from workflows import runner_orchestration_utils as rou

    seq = iter([("stale", "stale.json"), None])

    def _claim_reader():
        return next(seq)

    monkeypatch.setattr(rou, "try_read_double_process_claim", _claim_reader)
    monkeypatch.setattr(routes_workflows, "try_read_double_process_claim", _claim_reader)

    captured: list[str | None] = []

    def _run(on_progress=None, allow_angles_fallback=False, explicit_post_id=None):
        captured.append(explicit_post_id)
        return {
            "post_id": explicit_post_id or "x",
            "source_file": f"{explicit_post_id or 'x'}.json",
        }

    monkeypatch.setattr(api_v1_routes.runner, "run_double_process_new_post", _run)

    response = client.post(
        "/api/v1/workflows/double-process-new-post",
        json={"stream": False, "post_id": "mine"},
    )
    assert response.status_code == 200
    assert captured == ["mine"]


def test_double_process_posts_get_lists_reports(client, monkeypatch, tmp_path):
    from services import double_process_history as dph

    base = tmp_path / "dp"
    reports = base / "reports"
    reports.mkdir(parents=True)
    (reports / "r.json").write_text(
        json.dumps({"post_id": "p1", "succeeded": True, "mode": "double_process_new_post"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(dph, "double_process_cache_base_root", lambda: base)

    response = client.get("/api/v1/workflows/double-process-posts")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["count"] == 1
    assert data["base_path"] == str(base.resolve())
    assert data["runs"][0]["record"]["post_id"] == "p1"


def test_double_process_posts_get_filter_and_limit(client, monkeypatch, tmp_path):
    from services import double_process_history as dph

    base = tmp_path / "dp"
    reports = base / "reports"
    reports.mkdir(parents=True)
    (reports / "a.json").write_text(
        json.dumps({"post_id": "alpha", "succeeded": True}),
        encoding="utf-8",
    )
    (reports / "b.json").write_text(
        json.dumps({"post_id": "beta", "succeeded": True}),
        encoding="utf-8",
    )

    monkeypatch.setattr(dph, "double_process_cache_base_root", lambda: base)

    r = client.get("/api/v1/workflows/double-process-posts?post_id=beta&limit=5")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["count"] == 1
    assert data["runs"][0]["record"]["post_id"] == "beta"


def test_double_process_posts_invalid_limit_query(client):
    response = client.get("/api/v1/workflows/double-process-posts?limit=notint")
    assert response.status_code == 400
