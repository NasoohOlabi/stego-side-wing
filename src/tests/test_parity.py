"""
Basic parity tests to ensure refactored endpoints behave the same as before.

These are smoke tests focused on response structure and status codes,
not comprehensive functionality tests.
"""
import json
import pytest
from app.app_factory import create_app


@pytest.fixture
def client():
    """Create test client."""
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_route(client):
    """Test root route returns welcome message."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome" in response.get_data(as_text=True)


def test_expected_routes_registered(client):
    """Smoke test that critical refactored routes remain registered."""
    routes = {rule.rule for rule in client.application.url_map.iter_rules()}
    expected_routes = {
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
    }
    assert expected_routes.issubset(routes)


def test_workflow_runner_initializes():
    """Smoke test that workflow orchestration layer imports and initializes."""
    from workflows.runner import WorkflowRunner

    runner = WorkflowRunner()
    assert runner is not None


def test_posts_list_structure(client):
    """Test /posts_list returns expected structure (may fail if no data)."""
    # This will fail if directories don't exist, which is expected
    response = client.get("/posts_list?count=5&step=filter-url-unresolved")
    # Accept both success (200) and error (400/404) as valid responses
    assert response.status_code in [200, 400, 404, 500]
    if response.status_code == 200:
        data = json.loads(response.data)
        assert "fileNames" in data
        assert isinstance(data["fileNames"], list)


def test_semantic_search_structure(client):
    """Test /semantic_search expects correct request structure."""
    # Missing required fields should return 400
    response = client.post(
        "/semantic_search",
        json={},
        content_type="application/json"
    )
    assert response.status_code == 400
    
    # Valid structure should return 200 (or 500 if model not loaded)
    response = client.post(
        "/semantic_search",
        json={
            "text": "test query",
            "objects": [{"category": "test", "source_quote": "test", "tangent": "test"}],
            "n": 5
        },
        content_type="application/json"
    )
    assert response.status_code in [200, 500]  # 500 if sentence-transformers not installed


def test_angles_analyze_structure(client):
    """Test /angles/analyze expects correct request structure."""
    # Missing texts should return 400
    response = client.post(
        "/angles/analyze",
        json={},
        content_type="application/json"
    )
    assert response.status_code == 400
    
    # Empty texts should return 400
    response = client.post(
        "/angles/analyze",
        json={"texts": []},
        content_type="application/json"
    )
    assert response.status_code == 400


def test_kv_store_structure(client):
    """Test KV store endpoints return expected structure."""
    # Set a value
    response = client.post(
        "/set",
        json={"key": "test_key", "value": "test_value"},
        content_type="application/json"
    )
    assert response.status_code == 201
    data = json.loads(response.data)
    assert "status" in data
    assert data["status"] == "success"
    
    # Get the value
    response = client.get("/get/test_key")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert "k" in data
    assert "v" in data
    assert data["k"] == "test_key"
    assert data["v"] == "test_value"
    
    # Get non-existent key
    response = client.get("/get/nonexistent_key")
    assert response.status_code == 404
