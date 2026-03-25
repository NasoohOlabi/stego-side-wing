from types import SimpleNamespace

import pytest

from workflows.adapters.content import ContentAdapter
from workflows.contracts import FetchUrlResult
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline


def test_fetch_returns_original_result_when_adapter_fails():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    expected = FetchUrlResult(url="https://example.com", success=False, error="boom")
    pipeline.content_adapter = SimpleNamespace(
        fetch_url_content=lambda url, use_cache: expected,
        validate_content=lambda text: True,
    )

    result = pipeline.fetch("https://example.com")
    assert result is expected


def test_fetch_raises_after_validation_fails_across_fresh_attempts():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    pipeline.content_adapter = SimpleNamespace(
        fetch_url_content=lambda url, use_cache: FetchUrlResult(
            url=url, success=True, text="error 404 page not found"
        ),
        validate_content=lambda text: False,
    )

    with pytest.raises(RuntimeError, match="failed validation after 3 fresh fetches"):
        pipeline.fetch("https://example.com")


def test_fetch_retries_fresh_when_cached_content_fails_validation():
    real_validate = ContentAdapter().validate_content

    def fake_fetch(url: str, use_cache: bool) -> FetchUrlResult:
        if use_cache:
            return FetchUrlResult(url=url, success=True, text="404 not found error page")
        return FetchUrlResult(url=url, success=True, text="Clean article body.")

    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    pipeline.content_adapter = SimpleNamespace(
        fetch_url_content=fake_fetch,
        validate_content=real_validate,
    )

    result = pipeline.fetch("https://example.com", use_cache=True)
    assert result.success is True
    assert result.text == "Clean article body."


def test_fetch_returns_success_when_content_valid():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    expected = FetchUrlResult(url="https://example.com", success=True, text="real text")
    pipeline.content_adapter = SimpleNamespace(
        fetch_url_content=lambda url, use_cache: expected,
        validate_content=lambda text: True,
    )

    result = pipeline.fetch("https://example.com", summarize=True)
    assert result is expected
