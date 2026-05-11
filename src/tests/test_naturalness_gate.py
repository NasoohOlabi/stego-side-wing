from workflows.utils.naturalness_gate import (
    comment_plausibility_gate,
    filter_angles_for_post,
    score_angle_relevance,
)


def test_angle_relevance_allows_related_angle() -> None:
    post = {
        "title": "Starbucks closes hundreds of North America stores",
        "selftext": "The company says store closures are part of a restructuring plan.",
        "comments": [{"body": "This seems like a retail turnaround problem."}],
    }
    angle = {
        "source_quote": "Starbucks is closing hundreds of stores in North America.",
        "tangent": "Discuss how retail restructuring affects customer access.",
        "category": "Business",
    }

    score = score_angle_relevance(angle, post)

    assert score["passes"] is True
    assert "starbucks" in score["overlap_tokens"]


def test_angle_relevance_rejects_unrelated_angle() -> None:
    post = {
        "title": "Missouri grandmother held by ICE over old bad check",
        "selftext": "The family says the detention over a decade-old check is wrong.",
    }
    angle = {
        "source_quote": "Starbucks played a significant role in popularizing the latte.",
        "tangent": "Explore premium coffee branding in American retail.",
        "category": "Business",
    }

    score = score_angle_relevance(angle, post)

    assert score["passes"] is False
    assert "weak_post_relevance" in score["reasons"]


def test_angle_relevance_flags_source_quote_fragment() -> None:
    post = {"title": "Trump deploys National Guard to Washington DC"}
    angle = {
        "source_quote": "Authoritarian Influence on Democracies",
        "tangent": "Discuss authoritarian influence on democratic governance.",
        "category": "Politics",
    }

    filtered, report = filter_angles_for_post([angle], post)

    assert filtered == []
    assert report["rejected_count"] == 1
    assert report["reason_counts"]["source_quote_fragment"] == 1


def test_middle_gate_warns_but_keeps_fragment_with_topic_overlap() -> None:
    post = {
        "title": "Starbucks closes hundreds of stores",
        "selftext": "The closures are part of retail restructuring.",
    }
    angle = {
        "source_quote": "Starbucks store closures",
        "tangent": "Discuss Starbucks closures and retail restructuring.",
        "category": "Business",
    }

    filtered, report = filter_angles_for_post([angle], post)

    assert filtered == [angle]
    assert report["rejected_count"] == 0


def test_comment_plausibility_rejects_bare_fragment() -> None:
    result = comment_plausibility_gate("Download data to your computer")

    assert result["passes"] is False
    assert "title_like_fragment" in result["reasons"]


def test_comment_plausibility_rejects_canned_suffix() -> None:
    result = comment_plausibility_gate(
        "Starbucks is closing hundreds of stores. That seems directly relevant to what happened here."
    )

    assert result["passes"] is False
    assert "canned_generic_suffix" in result["reasons"]


def test_comment_plausibility_accepts_contextual_comment() -> None:
    result = comment_plausibility_gate(
        "If the store closures are tied to weak foot traffic, the turnaround probably needs more than a logo refresh."
    )

    assert result["passes"] is True
