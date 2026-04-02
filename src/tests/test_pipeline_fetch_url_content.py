from types import SimpleNamespace

import pytest

from workflows.adapters.content import ContentAdapter
from workflows.contracts import FetchUrlResult
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline

_ADAPTER = ContentAdapter()


def _mock_adapter(**kwargs: object) -> SimpleNamespace:
    base = {
        "content_validation_report": _ADAPTER.content_validation_report,
        "log_validation_failed": lambda **kw: None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_fetch_returns_original_result_when_adapter_fails():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    expected = FetchUrlResult(url="https://example.com", success=False, error="boom")
    pipeline.content_adapter = _mock_adapter(
        fetch_url_content=lambda url, use_cache: expected,
        validate_content=lambda text: True,
    )

    result = pipeline.fetch("https://example.com")
    assert result is expected


def test_fetch_raises_after_validation_fails_across_fresh_attempts():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    pipeline.content_adapter = _mock_adapter(
        fetch_url_content=lambda url, use_cache: FetchUrlResult(
            url=url, success=True, text="error 404 page not found"
        ),
        validate_content=lambda text: False,
    )

    with pytest.raises(RuntimeError, match="failed validation after 3 live refetches"):
        pipeline.fetch("https://example.com")


def test_fetch_retries_fresh_when_cached_content_fails_validation():
    real_validate = ContentAdapter().validate_content

    def fake_fetch(url: str, use_cache: bool) -> FetchUrlResult:
        if use_cache:
            return FetchUrlResult(url=url, success=True, text="404 not found error page")
        return FetchUrlResult(url=url, success=True, text="Clean article body.")

    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    pipeline.content_adapter = _mock_adapter(
        fetch_url_content=fake_fetch,
        validate_content=real_validate,
        log_validation_failed=_ADAPTER.log_validation_failed,
    )

    result = pipeline.fetch("https://example.com", use_cache=True)
    assert result.success is True
    assert result.text == "Clean article body."


def test_content_validation_report_flags_two_distinct_indicators():
    adapter = ContentAdapter()
    text = "Status 404 — page not found for this article"
    report = adapter.content_validation_report(text)
    assert report["passed"] is False
    assert report["reason"] == "error_indicator_heuristic"
    assert "404" in report["matched_indicators"]
    assert "not found" in report["matched_indicators"]
    assert report["distinct_indicator_count"] >= 2
    assert "text_sha256" in report


def test_fetch_returns_success_when_content_valid():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    expected = FetchUrlResult(url="https://example.com", success=True, text="real text")
    pipeline.content_adapter = _mock_adapter(
        fetch_url_content=lambda url, use_cache: expected,
        validate_content=lambda text: True,
    )

    result = pipeline.fetch("https://example.com", summarize=True)
    assert result is expected
