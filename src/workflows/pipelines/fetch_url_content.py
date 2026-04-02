"""Fetch and process URL content."""
from typing import Optional

from loguru import logger

from workflows.adapters.content import ContentAdapter
from workflows.contracts import FetchUrlResult

_MAX_FRESH_ATTEMPTS_AFTER_VALIDATION_FAILURE = 3

_LOG = logger.bind(component="FetchUrlContentPipeline")


class FetchUrlContentPipeline:
    """Pipeline for fetching URL content."""

    def __init__(self) -> None:
        self.content_adapter = ContentAdapter()

    def _fetch_raw(self, url: str, use_cache: bool) -> FetchUrlResult:
        return self.content_adapter.fetch_url_content(url, use_cache=use_cache)

    def _passes_validation(self, result: FetchUrlResult) -> bool:
        return (
            result.success
            and bool(result.text)
            and self.content_adapter.validate_content(result.text)
        )

    def _log_validation_reject(self, url: str, result: FetchUrlResult, phase: str) -> None:
        text = result.text if result.success else None
        if not result.success or not text:
            _LOG.bind(trace_id=None).warning(
                "fetch_url_content_validation_skipped_empty",
                url=url,
                phase=phase,
                fetch_success=result.success,
                fetch_error=result.error,
            )
            return
        self.content_adapter.log_validation_failed(url=url, text=text, phase=phase)

    def _finalize(self, result: FetchUrlResult, summarize: bool) -> FetchUrlResult:
        if summarize:
            pass
        return result

    def _recover_with_fresh_fetches(
        self, url: str, summarize: bool, initial_result: FetchUrlResult
    ) -> FetchUrlResult:
        self._log_validation_reject(url, initial_result, "initial_cached_or_first_fetch")
        for attempt in range(1, _MAX_FRESH_ATTEMPTS_AFTER_VALIDATION_FAILURE + 1):
            result = self._fetch_raw(url, False)
            if self._passes_validation(result):
                _LOG.bind(trace_id=None).info(
                    "fetch_url_content_validation_recovered",
                    url=url,
                    phase=f"recovery_attempt_{attempt}",
                )
                return self._finalize(result, summarize)
            self._log_validation_reject(
                url, result, f"recovery_attempt_{attempt}_disk_bypass"
            )
        raise RuntimeError(
            f"URL content failed validation after {_MAX_FRESH_ATTEMPTS_AFTER_VALIDATION_FAILURE} "
            f"live refetches (disk cache bypassed): {url}. "
            f"Search logs for fetch_url_content_validation_failed."
        )

    def fetch(
        self,
        url: str,
        use_cache: bool = True,
        summarize: bool = False,
    ) -> FetchUrlResult:
        """
        Fetch URL content.

        If cached (or first) HTML fails validation, retries up to three times with
        ``use_cache=False`` (skips adapter and analysis disk cache; live Jina/crawl4ai).
        Raises ``RuntimeError`` if validation still fails.
        """
        result = self._fetch_raw(url, use_cache)
        if not result.success or not result.text:
            return result
        if self._passes_validation(result):
            return self._finalize(result, summarize)
        return self._recover_with_fresh_fetches(url, summarize, result)
