"""Tests for the tangent-DB audit echo and receiver parity warning (plan 2, section 5)."""

from __future__ import annotations

from workflows.pipelines.receiver import (
    _tangent_db_parity_mismatch,  # pyright: ignore[reportPrivateUsage]
)
from workflows.pipelines.stego import (
    _sender_audit_from_post,  # pyright: ignore[reportPrivateUsage]
)

_REPORT = {
    "builder_version": "tangent_db_v1",
    "config": {"builder_version": "tangent_db_v1", "min_relevance": 0.12},
    "config_hash": "hash-sender",
}


def _post(with_report: bool) -> dict[str, object]:
    post: dict[str, object] = {
        "id": "p1",
        "title": "t",
        "selftext": "s",
        "comments": [],
        "search_results": [],
    }
    if with_report:
        post["tangent_db_report"] = dict(_REPORT)
    return post


def test_sender_audit_includes_tangent_db_report_when_post_carries_one() -> None:
    audit = _sender_audit_from_post(_post(with_report=True), {})
    assert audit["tangent_db_report"] == _REPORT


def test_sender_audit_omits_tangent_db_report_for_legacy_posts() -> None:
    audit = _sender_audit_from_post(_post(with_report=False), {})
    assert "tangent_db_report" not in audit


def test_parity_none_when_post_has_no_report() -> None:
    assert _tangent_db_parity_mismatch(_post(with_report=False), {}) is None


def test_parity_none_when_config_hashes_agree() -> None:
    receiver_report = {"tangent_db_report": {"config_hash": "hash-sender"}}
    assert _tangent_db_parity_mismatch(_post(with_report=True), receiver_report) is None


def test_parity_mismatch_when_receiver_hash_differs() -> None:
    receiver_report = {"tangent_db_report": {"config_hash": "hash-receiver"}}
    mismatch = _tangent_db_parity_mismatch(_post(with_report=True), receiver_report)
    assert mismatch == {
        "sender_config_hash": "hash-sender",
        "receiver_config_hash": "hash-receiver",
        "sender_config": _REPORT["config"],
    }


def test_parity_mismatch_when_receiver_ran_legacy_builder() -> None:
    mismatch = _tangent_db_parity_mismatch(_post(with_report=True), {})
    assert mismatch is not None
    assert mismatch["receiver_config_hash"] is None
    assert mismatch["sender_config_hash"] == "hash-sender"
