import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from requests.exceptions import RequestException

from workflows.adapters.backend_api import BackendAPIAdapter


def test_needle_finder_batch_falls_back_to_local_on_request_error(monkeypatch):
    adapter = BackendAPIAdapter(base_url="http://backend.invalid")
    expected = {"results": [{"index": 0, "best_match": "x", "score": 1.0}]}

    def fail_post(*args, **kwargs):
        raise RequestException("network down")

    monkeypatch.setattr("workflows.adapters.backend_api.requests.post", fail_post)
    monkeypatch.setattr(
        adapter, "_needle_finder_batch_local", lambda needles, haystack: expected
    )

    result = adapter.needle_finder_batch(["a"], ["x"])
    assert result == expected


def test_needle_finder_batch_local_handles_mixed_inputs(monkeypatch):
    adapter = BackendAPIAdapter(base_url="http://backend.invalid")

    def fake_find_best_match(needle, haystack):
        if needle == "bad":
            raise ValueError("bad haystack")
        if needle == "boom":
            raise RuntimeError("explode")
        return {"best_match": haystack[0], "index": 0, "score": 0.99}

    monkeypatch.setattr(
        "services.semantic_service.find_best_match", fake_find_best_match
    )

    result = adapter._needle_finder_batch_local(
        needles=["good", 123, "bad", "boom"],
        haystack=["alpha", "beta"],
    )

    assert len(result["results"]) == 4
    assert result["results"][0]["best_match"] == "alpha"
    assert result["results"][1]["error"] == "Failed to process needle '123': must be a string"
    assert result["results"][2]["error"] == "Failed to process needle 'bad': bad haystack"
    assert result["results"][3]["error"] == "Unexpected error processing needle 'boom': explode"


def test_save_post_local_requires_post_id(tmp_path):
    adapter = BackendAPIAdapter.__new__(BackendAPIAdapter)
    adapter.config = SimpleNamespace(get_step_dirs=lambda step: (tmp_path, tmp_path))

    with pytest.raises(ValueError, match="must include 'id' field"):
        adapter.save_post_local(post={"title": "missing id"}, step="angles-step")


def test_save_post_local_writes_json_file(tmp_path):
    adapter = BackendAPIAdapter.__new__(BackendAPIAdapter)
    dest_dir = tmp_path / "dest"
    adapter.config = SimpleNamespace(get_step_dirs=lambda step: (tmp_path, dest_dir))

    adapter.save_post_local(post={"id": "post-1", "title": "hello"}, step="angles-step")

    saved_file = dest_dir / "post-1.json"
    assert saved_file.exists()
    assert json.loads(saved_file.read_text(encoding="utf-8"))["title"] == "hello"


def test_save_object_local_writes_json_file(tmp_path):
    adapter = BackendAPIAdapter.__new__(BackendAPIAdapter)
    dest_dir = tmp_path / "objects"
    adapter.config = SimpleNamespace(get_step_dirs=lambda step: (tmp_path, dest_dir))

    adapter.save_object_local(
        data={"a": 1},
        step="angles-step",
        filename="obj.json",
    )

    saved_file = Path(dest_dir) / "obj.json"
    assert saved_file.exists()
    assert json.loads(saved_file.read_text(encoding="utf-8")) == {"a": 1}
