"""API routes for workflow LLM prompt get/update/reset."""
from __future__ import annotations

import json

import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_prompts_workflow_llm_get_ok(client, tmp_path, monkeypatch):
    from app.routes import api_v1_routes
    from workflows.utils import workflow_llm_prompts as wlp

    p = tmp_path / "workflow_llm_prompts.json"
    doc = wlp.default_workflow_llm_prompts()
    wlp.save_workflow_llm_prompts_to_path(p, doc)

    monkeypatch.setattr(wlp, "workflow_llm_prompts_path", lambda: p)
    monkeypatch.setattr(api_v1_routes, "workflow_llm_prompts_path", lambda: p)
    wlp.reload_prompts()

    response = client.get("/api/v1/prompts/workflow-llm")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    data = payload["data"]
    assert "stego_encode" in data["prompts"]
    assert data["prompts"]["stego_encode"]["system_template"] == doc.stego_encode.system_template


def test_prompts_workflow_llm_put_updates_file(client, tmp_path, monkeypatch):
    from app.routes import api_v1_routes
    from workflows.utils import workflow_llm_prompts as wlp

    p = tmp_path / "workflow_llm_prompts.json"
    doc = wlp.default_workflow_llm_prompts()
    wlp.save_workflow_llm_prompts_to_path(p, doc)

    monkeypatch.setattr(wlp, "workflow_llm_prompts_path", lambda: p)
    monkeypatch.setattr(api_v1_routes, "workflow_llm_prompts_path", lambda: p)
    wlp.reload_prompts()

    body = {
        "prompts": doc.model_dump(mode="json"),
    }
    body["prompts"]["version"] = 1
    body["prompts"]["gen_angles"]["system_template"] = "patched-system"

    response = client.put(
        "/api/v1/prompts/workflow-llm",
        data=json.dumps(body),
        content_type="application/json",
    )
    assert response.status_code == 201
    assert response.get_json()["ok"] is True

    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["gen_angles"]["system_template"] == "patched-system"

    wlp.reload_prompts()
    assert wlp.get_prompts().gen_angles.system_template == "patched-system"


def test_prompts_workflow_llm_put_invalid(client):
    response = client.put(
        "/api/v1/prompts/workflow-llm",
        data=json.dumps({"prompts": {"version": 0}}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_prompts_workflow_llm_reset(client, tmp_path, monkeypatch):
    from app.routes import api_v1_routes
    from workflows.utils import workflow_llm_prompts as wlp

    p = tmp_path / "workflow_llm_prompts.json"
    doc = wlp.default_workflow_llm_prompts()
    mutated = doc.model_copy(
        update={"gen_angles": doc.gen_angles.model_copy(update={"system_template": "bad"})}
    )
    wlp.save_workflow_llm_prompts_to_path(p, mutated)

    monkeypatch.setattr(wlp, "workflow_llm_prompts_path", lambda: p)
    monkeypatch.setattr(api_v1_routes, "workflow_llm_prompts_path", lambda: p)
    wlp.reload_prompts()

    response = client.post("/api/v1/prompts/workflow-llm/reset")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["gen_angles"]["system_template"] == doc.gen_angles.system_template


def test_format_gen_search_terms_user_prompt_orders_blocks():
    from workflows.utils import workflow_llm_prompts as wlp

    t = wlp.format_gen_search_terms_user_prompt("T", "Body", "https://x")
    assert "# Title: T" in t
    assert "`https://x`" in t
    assert "## Content:\nBody" in t
