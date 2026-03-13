"""Adapter for content fetching with explicit local/HTTP clients."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

from pipelines.headless_browser_analyzer import deterministic_hash_sha256, normalize_url
from workflows.config import WorkflowConfig, get_config
from workflows.contracts import FetchUrlResult


class LocalContentClient:
    """In-process content fetch client."""

    @staticmethod
    def fetch(url: str) -> Dict[str, Any]:
        from services.analysis_service import fetch_url_content_crawl4ai

        return fetch_url_content_crawl4ai(url)


class HttpContentClient:
    """HTTP content fetch client."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def fetch(self, url: str) -> Dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/fetch_url_content_crawl4ai",
            params={"url": url},
            timeout=100,
        )
        response.raise_for_status()
        return response.json()


class ContentAdapter:
    """Adapter for fetching and processing URL content."""

    def __init__(self, base_url: Optional[str] = None):
        self.config: WorkflowConfig = get_config()
        self.base_url = base_url or self.config.base_url
        self.local = LocalContentClient()
        self.http = HttpContentClient(self.base_url)

    def fetch_url_content(self, url: str, use_cache: bool = True) -> FetchUrlResult:
        if not url or not url.strip():
            return FetchUrlResult(url=url, success=False, error="Empty URL")
        url = url.strip()

        if use_cache:
            cached = self._get_cached_content(url)
            if cached:
                return cached

        try:
            api_response = self.local.fetch(url)
        except Exception:
            try:
                api_response = self.http.fetch(url)
            except Exception as e:
                return FetchUrlResult(url=url, success=False, error=f"Fetch error: {str(e)}")

        result_data = api_response.get("result", {})
        result = FetchUrlResult(
            url=url,
            success=result_data.get("success", False),
            text=result_data.get("text"),
            content_type=result_data.get("content_type"),
            error=result_data.get("error"),
        )
        if result.success and result.text:
            self._cache_content(url, api_response)
        return result

    def _get_cached_content(self, url: str) -> Optional[FetchUrlResult]:
        normalized_url = normalize_url(url)
        cache_key = deterministic_hash_sha256(normalized_url)
        cache_file = self.config.url_cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_response = json.load(f)
            result_data = cached_response.get("result", {})
            return FetchUrlResult(
                url=url,
                success=result_data.get("success", False),
                text=result_data.get("text"),
                content_type=result_data.get("content_type"),
                error=result_data.get("error"),
            )
        except Exception:
            return None

    def _cache_content(self, url: str, api_response: Dict[str, Any]) -> None:
        normalized_url = normalize_url(url)
        cache_key = deterministic_hash_sha256(normalized_url)
        cache_file = self.config.url_cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(api_response, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def validate_content(self, text: Optional[str]) -> bool:
        if not text:
            return False
        error_indicators = [
            "404",
            "not found",
            "page not found",
            "error",
            "access denied",
            "forbidden",
        ]
        text_lower = text.lower()
        error_count = sum(1 for token in error_indicators if token in text_lower)
        return error_count < 2
