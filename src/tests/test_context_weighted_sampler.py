from __future__ import annotations

import pytest

from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.utils.angle_artifact import (
    ParentConditionedAngleArtifact,
    assert_single_sampler_lane,
)
from workflows.utils.comment_context import ancestor_chain, index_comment_tree
from workflows.utils.context_research import rank_frozen_research
from workflows.utils.context_sampler import (
    ContextSamplerConfig,
    build_context_dictionary_bundle,
)


def _post() -> dict:
    return {
        "id": "p1",
        "title": "Python database transactions",
        "selftext": "How should a Python service manage database transactions?",
        "search_results": [
            {"id": "irrelevant", "text": "A recipe for baking sourdough bread."},
            {
                "id": "relevant",
                "url": "https://example.test/db",
                "text": "Python database transactions need rollback handling.",
            },
        ],
        "comments": [
            {
                "id": "c1",
                "parent_id": "t3_p1",
                "body": "Use a transaction context manager.",
                "replies": [
                    {
                        "id": "c2",
                        "parent_id": "t1_c1",
                        "body": "How should rollback errors be handled?",
                        "replies": [
                            {
                                "id": "c3",
                                "parent_id": "t1_c2",
                                "body": "Log the original exception.",
                                "replies": [],
                            }
                        ],
                    },
                    {
                        "id": "sibling",
                        "parent_id": "t1_c1",
                        "body": "Retries should be bounded.",
                        "replies": [],
                    },
                ],
            },
            {
                "id": "top",
                "parent_id": "t3_p1",
                "body": "Keep transaction scopes short.",
                "replies": [],
            },
        ],
    }


def _config(max_blocks: int = 10) -> ContextSamplerConfig:
    return ContextSamplerConfig(
        max_blocks=max_blocks,
        comment_cap=24,
        research_cap=8,
        comment_weight=3,
        research_weight=1,
        max_ancestors=8,
    )


def test_context_graph_uses_nearest_first_ancestors() -> None:
    index = index_comment_tree(_post())
    assert [node.comment_id for node in ancestor_chain(index, "c3", "p1")] == ["c2", "c1"]


def test_context_graph_rejects_duplicate_ids() -> None:
    post = _post()
    post["comments"].append({"id": "c1", "body": "duplicate", "replies": []})
    with pytest.raises(ValueError, match="Duplicate comment id"):
        index_comment_tree(post)


def test_sampler_is_parent_conditioned_bounded_and_deterministic() -> None:
    first = build_context_dictionary_bundle(_post(), "c2", _config())
    second = build_context_dictionary_bundle(_post(), "t1_c2", _config())
    assert first == second
    assert first["texts"][0] == _post()["selftext"]
    assert first["entries"][1]["comment_id"] == "c2"
    assert len(first["entries"]) <= 10
    assert first["report"]["dictionary_id"] == second["report"]["dictionary_id"]
    assert first["report"]["source_counts"]["comments"] > first["report"]["source_counts"]["search_results"]


def test_root_and_nested_parent_have_distinct_identity() -> None:
    root = build_context_dictionary_bundle(_post(), None, _config())
    nested = build_context_dictionary_bundle(_post(), "c2", _config())
    assert root["report"]["dictionary_id"] != nested["report"]["dictionary_id"]
    assert root["report"]["selected_parent_id"] is None


def test_missing_body_and_parent_fail() -> None:
    post = _post()
    post["selftext"] = ""
    with pytest.raises(ValueError, match="non-empty post body"):
        build_context_dictionary_bundle(post, None, _config())
    with pytest.raises(ValueError, match="Selected parent is missing"):
        build_context_dictionary_bundle(_post(), "missing", _config())


def test_research_ranking_changes_with_parent_terms() -> None:
    pool = _post()["search_results"]
    ranked = rank_frozen_research(pool, "Python rollback transaction")
    assert ranked[0].source_id == "relevant"


def test_missing_research_redistributes_to_comments() -> None:
    post = _post()
    post["search_results"] = []
    result = build_context_dictionary_bundle(post, "c2", _config())
    assert result["report"]["research_available"] is False
    assert result["report"]["source_counts"]["search_results"] == 0


def test_gen_angles_persists_parent_conditioned_artifact(monkeypatch) -> None:
    monkeypatch.setenv("WORKFLOW_CONTEXT_SAMPLER", "context_weighted_v2")
    monkeypatch.setenv("WORKFLOW_ANGLES_GENERATION_MODE", "extractive_zero_kld")
    output = GenAnglesPipeline().preview_post(_post(), selected_parent_id="c2")
    artifact = ParentConditionedAngleArtifact.model_validate(output["post"]["angle_artifact"])
    assert artifact.selected_parent_id == "c2"
    assert artifact.dictionary_id == output["report"]["dictionary_id"]
    assert artifact.tangent_hash == output["report"]["angles_hash"]


def test_mixed_sampler_lane_is_rejected() -> None:
    posts = [
        {"angle_artifact": {"sampler_version": "stable_round_robin_v1"}},
        {"angle_artifact": {"sampler_version": "context_weighted_v2"}},
    ]
    with pytest.raises(ValueError, match="Mixed angle sampler versions"):
        assert_single_sampler_lane(posts)
