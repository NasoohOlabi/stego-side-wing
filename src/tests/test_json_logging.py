"""Structured JSON logging: tags, trace_id, process_pid."""
from __future__ import annotations

import json
import logging

from infrastructure.json_logging import (
    TAG_API,
    TAG_FUNCTION,
    TAG_LIFECYCLE,
    TAG_PROCESS,
    TAG_TRACE,
    JsonFormatter,
    STRUCTURED_LOG_TAG_CATALOG,
    StructuredContextFilter,
    bind_trace_id,
    log_function_start,
    log_process_start,
    reset_trace_id,
    structured_log_tag_catalog,
    structured_log_tag_ids,
)


def test_structured_filter_merges_tags_and_trace() -> None:
    out: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            out.append(record)

    root = logging.getLogger("test_json_logging_filter")
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    h = _Capture()
    h.setFormatter(JsonFormatter())
    h.addFilter(StructuredContextFilter())
    root.addHandler(h)

    tok = bind_trace_id("trace-abc")
    try:
        root.info("hello", extra={"tags": ["custom"]})
    finally:
        reset_trace_id(tok)

    assert len(out) == 1
    line = JsonFormatter().format(out[0])
    data = json.loads(line)
    assert data["trace_id"] == "trace-abc"
    assert TAG_API in data["tags"]
    assert TAG_TRACE in data["tags"]
    assert "custom" in data["tags"]
    assert isinstance(data["process_pid"], int)


def test_log_helpers_set_event_and_tags() -> None:
    out: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            out.append(JsonFormatter().format(record))

    log = logging.getLogger("test_json_logging_helpers")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    h = _Capture()
    h.addFilter(StructuredContextFilter())
    log.addHandler(h)
    log.propagate = False

    log_function_start(log, "mymod.foo", x=1)
    log_process_start(log, "batch_job", job_id="j1")

    f = json.loads(out[0])
    assert f["event"] == "function.start"
    assert TAG_FUNCTION in f["tags"]
    assert TAG_LIFECYCLE in f["tags"]
    assert f["function"] == "mymod.foo"

    p = json.loads(out[1])
    assert p["event"] == "process.start"
    assert TAG_PROCESS in p["tags"]
    assert p["process_name"] == "batch_job"


def test_structured_log_tag_catalog_matches_constants() -> None:
    cat = structured_log_tag_catalog()
    assert len(cat) == len(STRUCTURED_LOG_TAG_CATALOG)
    assert structured_log_tag_ids() == [e["id"] for e in STRUCTURED_LOG_TAG_CATALOG]
    assert TAG_API == "api" and TAG_TRACE == "trace"
