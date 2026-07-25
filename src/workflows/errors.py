"""Typed workflow errors.

Before these existed, orchestration decided what to do about a failure by matching
substrings of the exception message -- whether to stop a run loop, whether to treat a
failure as a quota exhaustion, whether a receiver rebuild failed on fetch. That coupled
control flow to human-readable text that nothing stopped anyone from rewording.

Each class below carries the same message it always did, so existing logs, API payloads and
message-based checks are unchanged. The detectors in ``research``, ``runner`` and
``runner_orchestration_utils`` now test the type first and fall back to the substring, which
keeps callers that raise plain built-ins (including several tests) working.

``NoUnprocessedPostsError`` deliberately also subclasses ``ValueError``: it used to be raised
as one, and ``except ValueError`` handlers upstream must keep catching it.
"""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for errors raised by workflow orchestration."""


class NoUnprocessedPostsError(WorkflowError, ValueError):
    """No post remains for a step/tag combination.

    Normal loop termination for ``run_all`` style runs, not a fault.
    """


class QuotaExceededError(WorkflowError):
    """An upstream provider refused the request for quota or rate-limit reasons."""


class DataLoadFetchError(WorkflowError, RuntimeError):
    """URL content for a post could not be fetched or did not validate."""


class ReceiverDataLoadError(WorkflowError, RuntimeError):
    """Receiver context rebuild failed because data-load produced no usable body."""
