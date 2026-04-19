"""Parity checks between dedicated workflow routes and POST /workflows/run."""

from __future__ import annotations

from typing import Any

import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_workflows_run_gen_angles_calls_same_runner_as_dedicated_route(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routes import api_v1_routes

    calls: list[str] = []

    def _run_gen_angles(*_a: object, **_k: object) -> dict[str, Any]:
        calls.append("gen_angles")
        return {"ok": True}

    monkeypatch.setattr(api_v1_routes.runner, "run_gen_angles", _run_gen_angles)

    r1 = client.post("/api/v1/workflows/gen-angles", json={"count": 1, "offset": 0, "stream": False})
    assert r1.status_code == 200
    assert r1.get_json()["ok"] is True

    r2 = client.post(
        "/api/v1/workflows/run",
        json={"command": "gen-angles", "count": 1, "offset": 0, "stream": False},
    )
    assert r2.status_code == 200
    body = r2.get_json()
    assert body["ok"] is True
    assert body["data"]["command"] == "gen-angles"
    assert body["data"]["result"]["ok"] is True
    assert calls == ["gen_angles", "gen_angles"]


def test_workflows_run_prep_until_google_quota_then_stego_calls_same_runner_as_dedicated_route(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routes import api_v1_routes

    calls: list[str] = []

    def _run_prep(**_k: object) -> dict[str, Any]:
        calls.append("prep_until_google_quota_then_stego")
        return {"succeeded": True, "tag": "version_42"}

    monkeypatch.setattr(
        api_v1_routes.runner,
        "run_prep_until_google_quota_then_stego",
        _run_prep,
    )

    r1 = client.post(
        "/api/v1/workflows/prep-until-google-quota-then-stego",
        json={"tag": "version_42", "stream": False},
    )
    assert r1.status_code == 200
    assert r1.get_json()["ok"] is True

    r2 = client.post(
        "/api/v1/workflows/run",
        json={
            "command": "prep-until-google-quota-then-stego",
            "tag": "version_42",
            "stream": False,
        },
    )
    assert r2.status_code == 200
    body = r2.get_json()
    assert body["ok"] is True
    assert body["data"]["command"] == "prep-until-google-quota-then-stego"
    assert body["data"]["result"]["tag"] == "version_42"
    assert calls == [
        "prep_until_google_quota_then_stego",
        "prep_until_google_quota_then_stego",
    ]
