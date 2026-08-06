"""Reference-based quality metrics for paired steganography comparisons.

Optional model-backed metrics deliberately fail soft: a benchmark remains usable on
machines without the ``metrics`` dependency group or downloaded Hugging Face models.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_BERT_SCORERS: dict[tuple[str, str], Any] = {}
_SELF_CONSISTENCY_MODELS: dict[tuple[str, str | None], Any] = {}


def _warning(metric: str, exc: Exception | str) -> str:
    return f"{metric} unavailable: {exc}"


def score_reference_metrics(
    candidate: str,
    reference: str,
    *,
    bertscore_model: str = "roberta-large",
    bertscore_num_layers: int | None = None,
    device: str = "auto",
    include_bertscore: bool = True,
) -> dict[str, Any]:
    """Score one candidate against its matched human reference.

    Scores are ``None`` with an explicit warning when an optional dependency is
    absent. This is preferable to silently treating an unavailable score as zero.
    """
    result: dict[str, Any] = {
        "bleu": None,
        "rouge1": None,
        "rouge2": None,
        "rougeL": None,
        "bertscore_precision": None,
        "bertscore_recall": None,
        "bertscore_f1": None,
        "quality_metric_warnings": [],
        "quality_metric_provenance": {
            "reference_metric_version": "paired_reference_v1",
            "reference_text_present": bool(reference.strip()),
        },
    }
    if not candidate.strip() or not reference.strip():
        result["quality_metric_warnings"].append(
            "reference metrics skipped: empty candidate or reference"
        )
        return result
    try:
        import sacrebleu  # pyright: ignore[reportMissingImports]  # optional metrics extra

        result["bleu"] = float(sacrebleu.sentence_bleu(candidate, [reference]).score)
        result["quality_metric_provenance"]["bleu"] = "sacrebleu_sentence_bleu"
    except Exception as exc:  # optional dependency/model must not break a run
        result["quality_metric_warnings"].append(_warning("BLEU", exc))
    try:
        from rouge_score import rouge_scorer  # pyright: ignore[reportMissingImports]

        scores = rouge_scorer.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True).score(
            reference, candidate
        )
        result["rouge1"] = float(scores["rouge1"].fmeasure)
        result["rouge2"] = float(scores["rouge2"].fmeasure)
        result["rougeL"] = float(scores["rougeL"].fmeasure)
        result["quality_metric_provenance"]["rouge"] = "rouge-score_fmeasure_stemmed"
    except Exception as exc:
        result["quality_metric_warnings"].append(_warning("ROUGE", exc))
    if not include_bertscore:
        return result
    try:
        import torch
        from bert_score import BERTScorer  # pyright: ignore[reportMissingImports]  # optional extra

        resolved_device = device
        if device == "auto":
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        cache_key = (
            f"{bertscore_model}:{bertscore_num_layers}:rescale",
            resolved_device,
        )
        scorer = _BERT_SCORERS.get(cache_key)
        if scorer is None:
            scorer = BERTScorer(
                model_type=bertscore_model,
                lang="en",
                device=resolved_device,
                num_layers=bertscore_num_layers,
                rescale_with_baseline=True,
            )
            _BERT_SCORERS[cache_key] = scorer
        precision, recall, f1 = scorer.score([candidate], [reference])
        result["bertscore_precision"] = float(precision[0])
        result["bertscore_recall"] = float(recall[0])
        result["bertscore_f1"] = float(f1[0])
        result["quality_metric_provenance"]["bertscore"] = {
            "model": bertscore_model,
            "device": resolved_device,
            "num_layers": bertscore_num_layers,
            "rescale_with_baseline": True,
        }
    except Exception as exc:
        result["quality_metric_warnings"].append(_warning("BERTScore", exc))
    return result


def score_bertscore_pairs(
    candidates: list[str],
    references: list[str],
    *,
    model_name: str,
    num_layers: int | None = None,
    device: str = "auto",
    batch_size: int = 16,
) -> tuple[list[dict[str, float]] | None, str | None, dict[str, Any] | None]:
    """Batch BERTScore pairs to avoid re-running model inference for every row."""
    if len(candidates) != len(references):
        raise ValueError("candidates and references must have equal length")
    if not candidates:
        return [], None, None
    try:
        import torch
        from bert_score import BERTScorer  # pyright: ignore[reportMissingImports]  # optional extra

        resolved_device = "cuda" if device == "auto" and torch.cuda.is_available() else device
        if resolved_device == "auto":
            resolved_device = "cpu"
        cache_key = (f"{model_name}:{num_layers}:rescale", resolved_device)
        scorer = _BERT_SCORERS.get(cache_key)
        if scorer is None:
            scorer = BERTScorer(
                model_type=model_name,
                lang="en",
                device=resolved_device,
                num_layers=num_layers,
                rescale_with_baseline=True,
            )
            _BERT_SCORERS[cache_key] = scorer
        precision, recall, f1 = scorer.score(candidates, references, batch_size=batch_size)
        values = [
            {
                "bertscore_precision": float(p),
                "bertscore_recall": float(r),
                "bertscore_f1": float(f),
            }
            for p, r, f in zip(precision, recall, f1, strict=True)
        ]
        return (
            values,
            None,
            {
                "model": model_name,
                "num_layers": num_layers,
                "device": resolved_device,
                "rescale_with_baseline": True,
            },
        )
    except Exception as exc:
        return None, _warning("BERTScore", exc), None


def score_self_consistency(
    candidate: str,
    alternatives: Iterable[str],
    *,
    model_name: str = "all-MiniLM-L6-v2",
    device: str = "auto",
) -> tuple[float | None, str | None, dict[str, Any] | None]:
    """Mean cosine similarity to other same-post outputs for one method."""
    # Callers include the candidate itself. Remove exactly one matching occurrence;
    # additional identical outputs are real zero-diversity evidence (cosine = 1),
    # not candidates to discard.
    others: list[str] = []
    removed_self = False
    for text in alternatives:
        if not text.strip():
            continue
        if not removed_self and text == candidate:
            removed_self = True
            continue
        others.append(text)
    if not candidate.strip() or not others:
        return None, "Self-consistency skipped: no distinct same-post alternatives.", None
    try:
        from sentence_transformers import SentenceTransformer

        resolved_device = None if device == "auto" else device
        cache_key = (model_name, resolved_device)
        model = _SELF_CONSISTENCY_MODELS.get(cache_key)
        if model is None:
            model = SentenceTransformer(model_name, device=resolved_device)
            _SELF_CONSISTENCY_MODELS[cache_key] = model
        vectors = model.encode([candidate, *others], normalize_embeddings=True)
        candidate_vector = vectors[0]
        similarities = [
            sum(float(a) * float(b) for a, b in zip(candidate_vector, vector, strict=True))
            for vector in vectors[1:]
        ]
        return (
            float(sum(similarities) / len(similarities)),
            None,
            {"model": model_name, "alternatives": len(others), "metric": "mean_cosine_similarity"},
        )
    except Exception as exc:
        return None, _warning("Self-consistency", exc), None


def finite_quality_metric_keys() -> tuple[str, ...]:
    """Numeric, higher-is-better deterministic fields emitted by this module."""
    return (
        "bleu",
        "rouge1",
        "rouge2",
        "rougeL",
        "bertscore_precision",
        "bertscore_recall",
        "bertscore_f1",
    )


def diversity_metric_keys() -> tuple[str, ...]:
    """Numeric fields that are diagnostics, not quality scores.

    ``self_consistency`` is the mean similarity between different outputs for the same
    post. Treating it as higher-is-better inverts its meaning for steganography: high
    mutual similarity means low output diversity, hence less usable selection capacity
    and a more distinguishable generator. It is reported without a preferred direction.
    """
    return ("self_consistency",)


def aggregated_quality_metric_keys() -> tuple[str, ...]:
    """Every numeric field worth aggregating, regardless of direction."""
    return finite_quality_metric_keys() + diversity_metric_keys()
