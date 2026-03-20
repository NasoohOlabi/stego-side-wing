"""Tests for workflow run tracking and GET /api/v1/workflows/runs."""
import pytest

from app.app_factory import create_app
from services import workflow_run_tracker as tracker


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_tracker_register_and_end():
    rid = tracker.register_run("stego", "sync")
    snap = list(tracker.iter_snapshot())
    assert len(snap) == 1
    assert snap[0]["id"] == rid
    assert snap[0]["command"] == "stego"
    assert snap[0]["mode"] == "sync"
    assert "elapsed_ms" in snap[0]
    tracker.end_run(rid)
    assert list(tracker.iter_snapshot()) == []


def test_track_workflow_context():
    with tracker.track_workflow("decode"):
        snap = list(tracker.iter_snapshot())
        assert len(snap) == 1
        assert snap[0]["command"] == "decode"
    assert list(tracker.iter_snapshot()) == []


def test_workflows_runs_endpoint_empty(client):
    response = client.get("/api/v1/workflows/runs")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["data"]["count"] == 0
    assert body["data"]["runs"] == []


def test_workflows_runs_endpoint_lists_registered_run(client):
    rid = tracker.register_run("research", "sync")
    try:
        r = client.get("/api/v1/workflows/runs")
        assert r.status_code == 200
        data = r.get_json()["data"]
        assert data["count"] >= 1
        assert any(x["id"] == rid and x["command"] == "research" for x in data["runs"])
    finally:
        tracker.end_run(rid)
