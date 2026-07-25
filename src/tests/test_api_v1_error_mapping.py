"""Workflow failures must map to a status that says what the caller should do.

Previously every route collapsed everything into 500 with the raw exception text, so
"provider is rate-limiting you, retry later" and "this request will never work" were
indistinguishable.
"""

from __future__ import annotations

import pytest

from app.routes.api_v1.error_mapping import workflow_error_response
from services.workflow_facade import (
    DataLoadFetchError,
    NoUnprocessedPostsError,
    QuotaExceededError,
    ReceiverDataLoadError,
)


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_error"),
    [
        (NoUnprocessedPostsError("none left"), 409, "No unprocessed posts available"),
        (QuotaExceededError("429 from provider"), 503, "Upstream provider quota exceeded"),
        (DataLoadFetchError("fetch died"), 502, "Workflow data load failed"),
        (ReceiverDataLoadError("rebuild died"), 502, "Workflow data load failed"),
        # Anything untyped keeps the original behaviour.
        (RuntimeError("something unexpected"), 500, "Workflow execution failed"),
        (ValueError("bad input"), 500, "Workflow execution failed"),
    ],
)
def test_status_and_message_per_error_type(
    app_ctx, exc: Exception, expected_status: int, expected_error: str
) -> None:
    response, status = workflow_error_response(exc)

    assert status == expected_status
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"] == expected_error
    # The original message stays available for debugging, just not as the headline.
    assert body["details"] == str(exc)


@pytest.fixture
def app_ctx():
    """jsonify needs an application context."""
    from app.app_factory import create_app

    app = create_app()
    with app.app_context():
        yield


def test_untyped_exceptions_keep_the_legacy_shape(app_ctx) -> None:
    """The generic case is unchanged, so existing clients see what they always saw."""
    response, status = workflow_error_response(Exception("boom"))

    assert status == 500
    assert response.get_json()["error"] == "Workflow execution failed"
