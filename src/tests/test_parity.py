"""Contract tests for supported HTTP endpoints."""
import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_expected_routes_registered(client):
    routes = {rule.rule for rule in client.application.url_map.iter_rules()}
    assert {
        "/",
        "/posts_list",
        "/get_post",
        "/save_post",
        "/save_object",
        "/search",
        "/google_search",
        "/bing_search",
        "/ollama_search",
        "/process_file",
        "/fetch_url_content",
        "/fetch_url_content_crawl4ai",
        "/semantic_search",
        "/needle_finder",
        "/needle_finder_batch",
        "/angles/analyze",
        "/set",
        "/get/<k>",
    }.issubset(routes)


def test_index_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome" in response.get_data(as_text=True)


def test_posts_list_requires_count(client):
    response = client.get("/posts_list?step=filter-url-unresolved")
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Missing required query parameter: count",
    }


def test_posts_list_rejects_invalid_step(client):
    response = client.get("/posts_list?count=2&step=unknown-step")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid step: unknown-step"}


def test_posts_list_success_contract(client, monkeypatch):
    from app.routes import posts_routes

    monkeypatch.setattr(
        posts_routes,
        "list_posts",
        lambda count, step, tag, offset: {"fileNames": ["a.json", "b.json"]},
    )
    response = client.get("/posts_list?count=2&step=filter-url-unresolved")
    assert response.status_code == 200
    assert response.get_json() == {"fileNames": ["a.json", "b.json"]}


def test_get_post_not_found_maps_to_404(client, monkeypatch):
    from app.routes import posts_routes

    def _raise_not_found(post: str, step: str):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(posts_routes, "get_post", _raise_not_found)
    response = client.get("/get_post?post=x.json&step=filter-url-unresolved")
    assert response.status_code == 404
    assert response.get_json() == {"error": "missing"}


def test_save_post_requires_json_body(client):
    response = client.post("/save_post?step=filter-url-unresolved", data="not-json")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or missing JSON body"}


def test_save_post_success_contract(client, monkeypatch):
    from app.routes import posts_routes

    monkeypatch.setattr(
        posts_routes,
        "save_post",
        lambda post_data, step: {
            "success": True,
            "filename": "abc.json",
            "path": "/tmp/abc.json",
        },
    )
    response = client.post(
        "/save_post?step=filter-url-unresolved",
        json={"id": "abc", "title": "hello"},
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "filename": "abc.json",
        "path": "/tmp/abc.json",
    }


def test_save_object_propagates_validation_error(client, monkeypatch):
    from app.routes import posts_routes

    def _raise_error(data, step, filename):
        raise ValueError("bad filename")

    monkeypatch.setattr(posts_routes, "save_object", _raise_error)
    response = client.post(
        "/save_object?step=filter-url-unresolved&filename=bad/name.json",
        json={"x": 1},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "bad filename"}


def test_semantic_search_validation(client):
    response = client.post("/semantic_search", json={})
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Missing or invalid 'text' field (must be a string)",
    }


def test_semantic_search_success_contract(client, monkeypatch):
    from app.routes import semantic_routes

    monkeypatch.setattr(
        semantic_routes,
        "semantic_search",
        lambda text, objects, n: {
            "results": [{"object": {"id": 1}, "score": 0.9, "rank": 1}],
        },
    )
    response = client.post(
        "/semantic_search",
        json={"text": "needle", "objects": [{"id": 1}], "n": 1},
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "results": [{"object": {"id": 1}, "score": 0.9, "rank": 1}],
    }


def test_needle_finder_batch_mixed_inputs(client, monkeypatch):
    from app.routes import semantic_routes

    monkeypatch.setattr(
        semantic_routes,
        "find_best_match",
        lambda needle, haystack: {"best_match": haystack[0], "index": 0, "score": 1.0},
    )
    response = client.post(
        "/needle_finder_batch",
        json={"needles": ["ok", 123], "haystack": ["a", "b"]},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload["results"], list)
    assert payload["results"][0]["best_match"] == "a"
    assert "must be a string" in payload["results"][1]["error"]


def test_angles_analyze_success_contract(client, monkeypatch):
    from app.routes import angles_routes

    monkeypatch.setattr(
        angles_routes,
        "analyze_angles",
        lambda texts: [{"source_quote": texts[0], "tangent": "T", "category": "C"}],
    )
    response = client.post("/angles/analyze", json={"texts": ["hello"]})
    assert response.status_code == 200
    assert response.get_json() == {
        "results": [{"source_quote": "hello", "tangent": "T", "category": "C"}],
    }


def test_angles_analyze_validation(client):
    response = client.post("/angles/analyze", json={"texts": []})
    assert response.status_code == 400
    assert response.get_json() == {"error": "Provide at least one text block"}


def test_workflow_runner_initializes():
    from workflows.runner import WorkflowRunner

    assert WorkflowRunner() is not None


def test_kv_store_structure(client):
    set_response = client.post("/set", json={"key": "test_key", "value": "test_value"})
    assert set_response.status_code == 201
    assert set_response.get_json()["status"] == "success"

    get_response = client.get("/get/test_key")
    assert get_response.status_code == 200
    payload = get_response.get_json()
    assert payload["k"] == "test_key"
    assert payload["v"] == "test_value"

    missing_response = client.get("/get/nonexistent_key")
    assert missing_response.status_code == 404
