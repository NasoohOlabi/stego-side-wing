import sys
import types

import pytest

from services import semantic_service


class _Scalar:
    def __init__(self, value: float):
        self._value = value

    def item(self) -> float:
        return self._value


class _FakeModel:
    def encode(self, value, convert_to_tensor=True):
        if isinstance(value, str):
            return f"emb:{value}"
        return list(value)


@pytest.fixture
def fake_semantic_runtime(monkeypatch):
    def fake_cos_sim(_query_embedding, doc_embeddings):
        # Stable, deterministic "similarity": longer text => higher score.
        return [[_Scalar(float(len(str(doc)))) for doc in doc_embeddings]]

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.util = types.SimpleNamespace(cos_sim=fake_cos_sim)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(
        semantic_service, "get_semantic_model", lambda: _FakeModel()
    )


def test_semantic_search_rejects_missing_text():
    with pytest.raises(ValueError, match="Missing 'text' field"):
        semantic_service.semantic_search("", [{"category": "x"}])


def test_semantic_search_rejects_missing_objects():
    with pytest.raises(ValueError, match="Missing or invalid 'objects' field"):
        semantic_service.semantic_search("query", [])


def test_semantic_search_rejects_invalid_n():
    with pytest.raises(ValueError, match="'n' must be a valid integer or None"):
        semantic_service.semantic_search("query", [{"category": "x"}], n="abc")


def test_semantic_search_returns_ranked_and_limited_results(fake_semantic_runtime):
    objects = [
        {"category": "A", "source_quote": "short"},
        {"category": "LongCategory", "source_quote": "much longer quote"},
        {"misc": 42},
    ]

    result = semantic_service.semantic_search("anything", objects, n="2")

    assert len(result["results"]) == 2
    assert result["results"][0]["object"] == objects[1]
    assert result["results"][0]["rank"] == 1
    assert isinstance(result["results"][0]["score"], float)
    assert result["results"][1]["rank"] == 2


def test_find_best_match_rejects_bad_needle():
    with pytest.raises(ValueError, match="Missing or invalid 'needle' field"):
        semantic_service.find_best_match(needle=None, haystack=["a"])


def test_find_best_match_rejects_bad_haystack():
    with pytest.raises(ValueError, match="Missing or invalid 'haystack' field"):
        semantic_service.find_best_match(needle="x", haystack="not-a-list")


def test_find_best_match_rejects_all_empty_haystack():
    with pytest.raises(ValueError, match="All haystack items are empty"):
        semantic_service.find_best_match(needle="x", haystack=["", ""])


def test_find_best_match_returns_highest_score(fake_semantic_runtime):
    haystack = ["tiny", "this is longer", "mid"]

    result = semantic_service.find_best_match("needle", haystack)

    assert result["best_match"] == "this is longer"
    assert result["index"] == 1
    assert isinstance(result["score"], float)
