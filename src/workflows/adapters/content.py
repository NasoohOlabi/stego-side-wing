"""Adapter for content fetching and summarization."""
import json
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from event_loop_manager import run_async
from headless_browser_analyzer import deterministic_hash_sha256, normalize_url
from scraper import extract_structured_data
from workflows.config import get_config
from workflows.contracts import FetchUrlResult


class ContentAdapter:
    """Adapter for fetching and processing URL content."""
    
    def __init__(self, base_url: Optional[str] = None):
        self.config = get_config()
        self.base_url = base_url or self.config.base_url
    
    def fetch_url_content(
        self, url: str, use_cache: bool = True
    ) -> FetchUrlResult:
        """
        Fetch URL content using the backend API.
        
        Returns FetchUrlResult with success status and text content.
        """
        if not url or not url.strip():
            return FetchUrlResult(url=url, success=False, error="Empty URL")
        
        url = url.strip()
        
        # Check cache first
        if use_cache:
            cached = self._get_cached_content(url)
            if cached:
                return cached
        
        # Fetch via backend API
        try:
            params = {"url": url}
            response = requests.post(
                f"{self.base_url}/fetch_url_content_crawl4ai",
                params=params,
                timeout=100,
            )
            response.raise_for_status()
            api_response = response.json()
            
            result_data = api_response.get("result", {})
            success = result_data.get("success", False)
            text = result_data.get("text")
            content_type = result_data.get("content_type")
            error = result_data.get("error")
            
            result = FetchUrlResult(
                url=url,
                success=success,
                text=text,
                content_type=content_type,
                error=error,
            )
            
            # Cache successful results
            if success and text:
                self._cache_content(url, api_response)
            
            return result
            
        except Exception as e:
            return FetchUrlResult(
                url=url, success=False, error=f"Fetch error: {str(e)}"
            )
    
    def _get_cached_content(self, url: str) -> Optional[FetchUrlResult]:
        """Get cached content if available."""
        normalized_url = normalize_url(url)
        cache_key = deterministic_hash_sha256(normalized_url)
        cache_file = self.config.url_cache_dir / f"{cache_key}.json"
        
        if cache_file.exists():
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
                pass
        return None
    
    def _cache_content(self, url: str, api_response: Dict[str, Any]) -> None:
        """Cache API response."""
        normalized_url = normalize_url(url)
        cache_key = deterministic_hash_sha256(normalized_url)
        cache_file = self.config.url_cache_dir / f"{cache_key}.json"
        
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(api_response, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    
    def validate_content(self, text: Optional[str]) -> bool:
        """
        Validate fetched content to detect error pages.
        
        Returns True if content is valid, False if it's an error page.
        """
        if not text:
            return False
        
        # Check for common error indicators
        error_indicators = [
            "404",
            "not found",
            "page not found",
            "error",
            "access denied",
            "forbidden",
        ]
        
        text_lower = text.lower()
        # Only fail if multiple indicators are present (to avoid false positives)
        error_count = sum(1 for indicator in error_indicators if indicator in text_lower)
        return error_count < 2
