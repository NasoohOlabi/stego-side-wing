"""GET /api/v1/logging/tags returns the structured log tag catalog."""
from __future__ import annotations

import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_logging_tags_ok(client):
    response = client.get("/api/v1/logging/tags")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    data = payload["data"]
    assert "tags" in data and "tag_ids" in data
    ids = [t["id"] for t in data["tags"]]
    assert ids == data["tag_ids"]
    assert "api" in ids
    assert all("id" in t and "description" in t for t in data["tags"])
