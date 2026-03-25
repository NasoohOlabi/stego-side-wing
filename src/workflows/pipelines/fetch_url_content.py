"""Fetch and process URL content."""
from typing import Optional

from workflows.adapters.content import ContentAdapter
from workflows.contracts import FetchUrlResult

_MAX_FRESH_ATTEMPTS_AFTER_VALIDATION_FAILURE = 3


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

    def _finalize(self, result: FetchUrlResult, summarize: bool) -> FetchUrlResult:
        if summarize:
            pass
        return result

    def _recover_with_fresh_fetches(self, url: str, summarize: bool) -> FetchUrlResult:
        for _ in range(_MAX_FRESH_ATTEMPTS_AFTER_VALIDATION_FAILURE):
            result = self._fetch_raw(url, False)
            if self._passes_validation(result):
                return self._finalize(result, summarize)
        raise RuntimeError(
            f"URL content failed validation after {_MAX_FRESH_ATTEMPTS_AFTER_VALIDATION_FAILURE} "
            f"fresh fetches: {url}"
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
        ``use_cache=False``. Raises ``RuntimeError`` if validation still fails.
        """
        result = self._fetch_raw(url, use_cache)
        if not result.success or not result.text:
            return result
        if self._passes_validation(result):
            return self._finalize(result, summarize)
        return self._recover_with_fresh_fetches(url, summarize)
