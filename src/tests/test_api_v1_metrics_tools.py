import pytest

from app.app_factory import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_metrics_perplexity_endpoint_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "run_perplexity_metrics",
        lambda *a, **k: {
            "report": {"ok": True},
            "report_path": "/repo/metrics/perplexity_metrics_test.json",
        },
    )
    r = client.post("/api/v1/tools/metrics/perplexity", json={})
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    assert payload["data"]["report_path"].endswith(".json")


def test_metrics_history_endpoint_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "list_metrics_history",
        lambda *a, **k: [
            {
                "kind": "perplexity",
                "filename": "perplexity_metrics_x.json",
                "path": "metrics/perplexity_metrics_x.json",
                "size_bytes": 10,
                "updated_at_utc": "2026-01-01T00:00:00+00:00",
            }
        ],
    )
    r = client.get("/api/v1/tools/metrics/history?type=perplexity&limit=5")
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    assert payload["data"]["count"] == 1
    assert payload["data"]["history"][0]["kind"] == "perplexity"


def test_metrics_divergence_endpoint_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "run_divergence_metrics",
        lambda *a, **k: {
            "report": {"ok": True},
            "report_path": "/repo/metrics/divergence_metrics_test.json",
        },
    )
    r = client.post("/api/v1/tools/metrics/divergence", json={})
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    assert payload["data"]["report_path"].endswith(".json")


def test_metrics_single_post_endpoint_ok(client, monkeypatch):
    from app.routes import api_v1_routes

    monkeypatch.setattr(
        api_v1_routes,
        "run_single_post_metrics",
        lambda *a, **k: {
            "file": "output-results/x_version_1.json",
            "post_id": "x",
            "perplexity": 10.0,
            "resolved_device": "cpu",
            "primary_baseline_matched_post": None,
            "secondary_baseline_global_corpus": {"kl_stego_vs_global_corpus": 0.1, "jsd_stego_vs_global_corpus": 0.05},
            "warnings": [],
            "config": {},
        },
    )
    r = client.post(
        "/api/v1/tools/metrics/post",
        json={"filename": "x_version_1.json"},
    )
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["ok"] is True
    assert payload["data"]["post_id"] == "x"
    assert payload["data"]["perplexity"] == 10.0


def test_metrics_single_post_rejects_path_in_filename(client) -> None:
    r = client.post(
        "/api/v1/tools/metrics/post",
        json={"filename": "subdir/x_version_1.json"},
    )
    assert r.status_code == 400
