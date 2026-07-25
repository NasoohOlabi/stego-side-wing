"""One place that turns a workflow failure into an HTTP response.

Every workflow route used to end with the same three lines::

    except Exception as exc:
        return fail("Workflow execution failed", status=500, details=str(exc))

Fourteen copies, all collapsing bad input, an upstream provider refusing service and a
genuine internal bug into the same 500. Callers could not tell "retry in a minute" from
"this will never work".

Error types come through ``services.workflow_facade`` rather than ``workflows.errors``
directly, because ``app`` is not allowed to import ``workflows`` (see
``docs/development/architecture-layers.md``, enforced by ``.importlinter``).
"""

from __future__ import annotations

from flask import Response

from app.schemas.responses import fail
from services.workflow_facade import (
    DataLoadFetchError,
    NoUnprocessedPostsError,
    QuotaExceededError,
    ReceiverDataLoadError,
)


def workflow_error_response(exc: Exception) -> tuple[Response, int]:
    """Map a workflow exception onto an HTTP status.

    Anything untyped keeps the original 500 and message, so clients that only knew the
    generic shape are unaffected.
    """
    if isinstance(exc, NoUnprocessedPostsError):
        # Nothing left to process is a state conflict, not a server fault; retrying the
        # identical request will keep returning the same thing.
        return fail("No unprocessed posts available", status=409, details=str(exc))
    if isinstance(exc, QuotaExceededError):
        # Upstream provider refused for quota/rate reasons: worth retrying later.
        return fail("Upstream provider quota exceeded", status=503, details=str(exc))
    if isinstance(exc, DataLoadFetchError | ReceiverDataLoadError):
        # We are the client of a failing upstream fetch, so this is a bad gateway.
        return fail("Workflow data load failed", status=502, details=str(exc))
    return fail("Workflow execution failed", status=500, details=str(exc))
