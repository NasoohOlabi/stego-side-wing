import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_protocol_gen_terms_endpoint(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes.runner.backend,
        "get_post_local",
        lambda post_filename, step: {"id": "abc", "title": "hello", "url": "https://example.com"},
    )
    monkeypatch.setattr(
        api_v1_routes.runner.gen_terms,
        "preview_generation",
        lambda **kwargs: {"post_id": kwargs["post_id"], "terms": ["a", "b"], "used_cache": False},
    )

    response = client.post(
        "/api/v1/tools/protocol/gen-terms",
        json={"post_id": "abc", "use_cache": False, "persist_cache": False},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["terms"] == ["a", "b"]


def test_protocol_gen_terms_endpoint_preserves_failure_metadata(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes.runner.backend,
        "get_post_local",
        lambda post_filename, step: {"id": "abc", "title": "hello", "url": "https://example.com"},
    )
    monkeypatch.setattr(
        api_v1_routes.runner.gen_terms,
        "preview_generation",
        lambda **kwargs: {
            "post_id": kwargs["post_id"],
            "terms": [],
            "used_cache": False,
            "cache_hit": False,
            "cache_error": "cache root must be array",
            "retry_count": 2,
            "elapsed_ms": 321,
            "error": "503 Server Error",
            "error_kind": "HTTPError",
        },
    )

    response = client.post(
        "/api/v1/tools/protocol/gen-terms",
        json={"post_id": "abc", "use_cache": False, "persist_cache": False},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["error_kind"] == "HTTPError"
    assert payload["data"]["retry_count"] == 2
    assert payload["data"]["cache_error"] == "cache root must be array"
    assert payload["data"]["elapsed_ms"] == 321


def test_protocol_research_preview_endpoint(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes.runner,
        "preview_data_load_post",
        lambda post_id, use_cache=False: {
            "post": {"id": post_id, "selftext": "x"},
            "report": {"fetch_success": True},
        },
    )
    monkeypatch.setattr(
        api_v1_routes.runner,
        "preview_research_post",
        lambda post_id, source_post=None, **kwargs: {
            "post": {"id": post_id, "search_results": ["r1"]},
            "report": {"search_results_count": 1},
        },
    )

    response = client.post(
        "/api/v1/tools/protocol/research-preview",
        json={"post_id": "abc"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["research"]["report"]["search_results_count"] == 1


def test_protocol_angles_preview_endpoint(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes.runner,
        "preview_data_load_post",
        lambda post_id, use_cache=False: {
            "post": {"id": post_id, "selftext": "x"},
            "report": {"fetch_success": True},
        },
    )
    monkeypatch.setattr(
        api_v1_routes.runner,
        "preview_research_post",
        lambda post_id, source_post=None, **kwargs: {
            "post": {"id": post_id, "search_results": ["r1"]},
            "report": {},
        },
    )
    monkeypatch.setattr(
        api_v1_routes.runner,
        "preview_gen_angles_post",
        lambda post_id, source_post=None, **kwargs: {
            "post": {"id": post_id, "angles": [{"source_quote": "q", "tangent": "t", "category": "c"}]},
            "report": {"options_count": 1},
        },
    )

    response = client.post(
        "/api/v1/tools/protocol/angles-preview",
        json={"post_id": "abc"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["data"]["gen_angles"]["report"]["options_count"] == 1
