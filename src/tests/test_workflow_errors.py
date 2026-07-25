"""Tests for the typed workflow errors and the detectors that consume them.

The point of these classes is to stop control flow depending on exception message text.
The detectors keep a substring fallback for callers that still raise built-ins, so both
routes are covered here -- if the type check silently stopped working, the fallback would
hide it.
"""

from __future__ import annotations

import pytest

from workflows.errors import (
    DataLoadFetchError,
    NoUnprocessedPostsError,
    QuotaExceededError,
    ReceiverDataLoadError,
    WorkflowError,
)
from workflows.pipelines.research import is_likely_google_quota_error
from workflows.runner import WorkflowRunner, _is_no_unprocessed_posts
from workflows.runner_orchestration_utils import is_receiver_data_load_failure


def test_every_error_shares_the_workflow_base() -> None:
    for cls in (
        NoUnprocessedPostsError,
        QuotaExceededError,
        DataLoadFetchError,
        ReceiverDataLoadError,
    ):
        assert issubclass(cls, WorkflowError)


def test_no_unprocessed_posts_is_still_a_value_error() -> None:
    """It used to be raised as ValueError; `except ValueError` callers must still catch it."""
    assert issubclass(NoUnprocessedPostsError, ValueError)
    with pytest.raises(ValueError):
        raise NoUnprocessedPostsError("none left")


def test_fetch_errors_are_still_runtime_errors() -> None:
    assert issubclass(DataLoadFetchError, RuntimeError)
    assert issubclass(ReceiverDataLoadError, RuntimeError)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (NoUnprocessedPostsError("anything at all"), True),
        (ValueError("No unprocessed posts found for step='final-step'"), True),
        (ValueError("something else entirely"), False),
    ],
)
def test_no_unprocessed_posts_detector_accepts_type_and_legacy_message(
    exc: Exception, expected: bool
) -> None:
    assert _is_no_unprocessed_posts(exc) is expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (QuotaExceededError("anything at all"), True),
        (RuntimeError("HTTP 429 rate limit"), True),
        (RuntimeError("resource exhausted"), True),
        (RuntimeError("connection refused"), False),
    ],
)
def test_quota_detector_accepts_type_and_legacy_message(exc: Exception, expected: bool) -> None:
    assert is_likely_google_quota_error(exc) is expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (DataLoadFetchError("anything at all"), True),
        (RuntimeError("Failed to fetch URL content for post p1: timeout"), True),
        (RuntimeError("unrelated"), False),
    ],
)
def test_data_load_fetch_detector_accepts_type_and_legacy_message(
    exc: Exception, expected: bool
) -> None:
    assert WorkflowRunner._is_data_load_fetch_failure(exc) is expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ReceiverDataLoadError("anything at all"), True),
        (RuntimeError("Receiver data-load failed for post p1"), True),
        (RuntimeError("unrelated"), False),
    ],
)
def test_receiver_data_load_detector_accepts_type_and_legacy_message(
    exc: Exception, expected: bool
) -> None:
    assert is_receiver_data_load_failure(exc) is expected
