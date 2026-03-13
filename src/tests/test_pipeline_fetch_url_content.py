from types import SimpleNamespace

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


def test_fetch_rejects_error_page_content():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    pipeline.content_adapter = SimpleNamespace(
        fetch_url_content=lambda url, use_cache: FetchUrlResult(
            url=url, success=True, text="error 404 page not found"
        ),
        validate_content=lambda text: False,
    )

    result = pipeline.fetch("https://example.com")
    assert result.success is False
    assert result.error == "Content validation failed (error page detected)"


def test_fetch_returns_success_when_content_valid():
    pipeline = FetchUrlContentPipeline.__new__(FetchUrlContentPipeline)
    expected = FetchUrlResult(url="https://example.com", success=True, text="real text")
    pipeline.content_adapter = SimpleNamespace(
        fetch_url_content=lambda url, use_cache: expected,
        validate_content=lambda text: True,
    )

    result = pipeline.fetch("https://example.com", summarize=True)
    assert result is expected
