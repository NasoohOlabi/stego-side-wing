from types import SimpleNamespace

from workflows.pipelines.decode import DecodePipeline


def test_decode_returns_none_when_no_angles():
    pipeline = DecodePipeline.__new__(DecodePipeline)
    assert pipeline.decode("stego", []) is None


def test_decode_prefers_labeled_idx_in_verbose_response():
    """Prose may contain other digits; idx: N should win."""
    angles = [
        {"source_quote": "q0", "tangent": "t0"},
        {"source_quote": "q1", "tangent": "t1"},
        {"source_quote": "q2", "tangent": "t2"},
    ]
    pipeline = DecodePipeline.__new__(DecodePipeline)
    pipeline.backend = SimpleNamespace(
        semantic_search=lambda text, objects, n: {"results": [{"object": angles[1]}]}
    )
    verbose = (
        "Step 1: consider candidates 0 1 2. Scores vary. "
        "The best match is angle 1. idx: 1"
    )
    pipeline.llm = SimpleNamespace(call_llm=lambda **kwargs: verbose)

    assert pipeline.decode("message", angles) == 1


def test_decode_uses_llm_integer_when_valid_candidate():
    angles = [
        {"source_quote": "q0", "tangent": "t0"},
        {"source_quote": "q1", "tangent": "t1"},
        {"source_quote": "q2", "tangent": "t2"},
    ]
    pipeline = DecodePipeline.__new__(DecodePipeline)
    pipeline.backend = SimpleNamespace(
        semantic_search=lambda text, objects, n: {"results": [{"object": angles[1]}]}
    )
    pipeline.llm = SimpleNamespace(call_llm=lambda **kwargs: "1")

    assert pipeline.decode("message", angles) == 1


def test_decode_interprets_rank_when_llm_returns_rank_not_index():
    angles = [
        {"source_quote": "q0", "tangent": "t0"},
        {"source_quote": "q1", "tangent": "t1"},
        {"source_quote": "q2", "tangent": "t2"},
    ]
    pipeline = DecodePipeline.__new__(DecodePipeline)
    pipeline.backend = SimpleNamespace(
        semantic_search=lambda text, objects, n: {
            "results": [{"object": angles[2]}, {"object": angles[0]}]
        }
    )
    pipeline.llm = SimpleNamespace(call_llm=lambda **kwargs: "1")

    assert pipeline.decode("message", angles) == 2


def test_decode_falls_back_to_top_semantic_match_when_llm_invalid():
    angles = [
        {"source_quote": "q0", "tangent": "t0"},
        {"source_quote": "q1", "tangent": "t1"},
    ]
    pipeline = DecodePipeline.__new__(DecodePipeline)
    pipeline.backend = SimpleNamespace(
        semantic_search=lambda text, objects, n: {"results": [{"object": angles[1]}]}
    )
    pipeline.llm = SimpleNamespace(call_llm=lambda **kwargs: "not a number")

    assert pipeline.decode("message", angles) == 1


def test_decode_returns_none_on_runtime_error():
    angles = [{"source_quote": "q0", "tangent": "t0"}]
    pipeline = DecodePipeline.__new__(DecodePipeline)
    pipeline.backend = SimpleNamespace(
        semantic_search=lambda text, objects, n: (_ for _ in ()).throw(RuntimeError("down"))
    )
    pipeline.llm = SimpleNamespace(call_llm=lambda **kwargs: "0")

    assert pipeline.decode("message", angles) is None
