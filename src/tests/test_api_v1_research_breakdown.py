"""Tests for research workflow include_breakdown via /workflows/run and /workflows/research."""
from __future__ import annotations

import pytest

from app.app_factory import create_app
from app.routes import api_v1_routes as routes


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def _fake_run_research(*, include_breakdown: bool = False, **_kwargs):
    if include_breakdown:
        return {
            "posts": [{"id": "p1"}],
            "breakdown": {
                "batch": {
                    "elapsed_ms": 42,
                    "processed_count": 1,
                    "requested_count": 1,
                    "offset": 0,
                    "runner_trace_id": "tid",
                    "preview_total_ms_sum": 10,
                },
                "posts": [
                    {"post_id": "p1", "report": {"timing": {"preview_total_ms": 10}}}
                ],
            },
        }
    return [{"id": "p1"}]


def test_workflows_run_research_include_breakdown(client, monkeypatch):
    monkeypatch.setattr(routes.runner, "run_research", _fake_run_research)
    response = client.post(
        "/api/v1/workflows/run",
        json={
            "command": "research",
            "count": 1,
            "offset": 0,
            "stream": False,
            "include_breakdown": True,
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    result = body["data"]["result"]
    assert result["posts"] == [{"id": "p1"}]
    assert result["breakdown"]["batch"]["runner_trace_id"] == "tid"
    assert result["breakdown"]["posts"][0]["post_id"] == "p1"


def test_workflows_run_research_default_list_shape(client, monkeypatch):
    monkeypatch.setattr(routes.runner, "run_research", _fake_run_research)
    response = client.post(
        "/api/v1/workflows/run",
        json={"command": "research", "count": 1, "stream": False},
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["result"] == [{"id": "p1"}]


def test_workflows_research_include_breakdown(client, monkeypatch):
    monkeypatch.setattr(routes.runner, "run_research", _fake_run_research)
    response = client.post(
        "/api/v1/workflows/research",
        json={"count": 1, "offset": 0, "stream": False, "include_breakdown": True},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["posts"] == [{"id": "p1"}]
    assert body["data"]["breakdown"]["batch"]["elapsed_ms"] == 42
