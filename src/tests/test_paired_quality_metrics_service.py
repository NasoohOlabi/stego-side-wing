from services import paired_quality_metrics_service as service
from services.paired_quality_metrics_service import (
    aggregated_quality_metric_keys,
    diversity_metric_keys,
    finite_quality_metric_keys,
    score_reference_metrics,
    score_self_consistency,
)


class _FakeEmbeddingModel:
    def encode(self, texts: list[str], *, normalize_embeddings: bool) -> list[list[float]]:
        assert normalize_embeddings is True
        return [[1.0, 0.0] for _ in texts]


def test_empty_reference_returns_null_scores_without_loading_models() -> None:
    result = score_reference_metrics("candidate", "")
    assert result["bleu"] is None
    assert result["bertscore_f1"] is None
    assert result["quality_metric_warnings"]


def test_self_consistency_requires_distinct_same_post_outputs() -> None:
    score, warning, provenance = score_self_consistency("same", ["same"])
    assert score is None
    assert warning is not None
    assert provenance is None


def test_self_consistency_counts_additional_identical_outputs() -> None:
    service._SELF_CONSISTENCY_MODELS[("fake", None)] = _FakeEmbeddingModel()

    score, warning, provenance = score_self_consistency(
        "same", ["same", "same"], model_name="fake"
    )

    assert score == 1.0
    assert warning is None
    assert provenance and provenance["alternatives"] == 1


def test_quality_metric_field_set_is_complete() -> None:
    assert {"bleu", "rougeL", "bertscore_f1"}.issubset(finite_quality_metric_keys())
    assert {"bleu", "rougeL", "bertscore_f1", "self_consistency"}.issubset(
        aggregated_quality_metric_keys()
    )


def test_bertscorer_requests_baseline_rescaling(monkeypatch) -> None:
    import sys
    import types

    captured: dict[str, object] = {}

    class _FakeScorer:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def score(self, candidates: list[str], references: list[str]):
            assert candidates and references
            return ([0.1], [0.2], [0.3])

    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = types.SimpleNamespace(is_available=lambda: False)  # type: ignore[attr-defined]
    bert_mod = types.ModuleType("bert_score")
    bert_mod.BERTScorer = _FakeScorer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "bert_score", bert_mod)
    service._BERT_SCORERS.clear()

    result = score_reference_metrics("candidate text here", "reference text here")

    assert captured.get("rescale_with_baseline") is True
    assert result["bertscore_f1"] == 0.3
    assert result["quality_metric_provenance"]["bertscore"]["rescale_with_baseline"] is True


def test_self_consistency_is_not_a_higher_is_better_quality_score() -> None:
    """High similarity between same-post outputs means low diversity, not high quality."""
    assert "self_consistency" not in finite_quality_metric_keys()
    assert "self_consistency" in diversity_metric_keys()
