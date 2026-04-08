import asyncio
import json
import os
import threading
import time
from typing import Any, Dict, Optional, Type, cast

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from loguru import logger
from pydantic import BaseModel

from infrastructure.config import get_lm_studio_url

# --- Configuration Constants ---
LM_STUDIO_URL = get_lm_studio_url()

API_TOKEN = "lm-studio"  # Placeholder for local server

# Custom JavaScript to click common "Accept Cookies" buttons if Magic Mode misses them
JS_CONSENT_CLICK = """
(function() {
    const selectors = [
        '#accept', '#onetrust-accept-btn-handler', '#agree', 
        '.cookie-accept', '.accept-cookies', 'button[aria-label="Accept"]',
        'button:contains("Accept")', 'button:contains("Agree")'
    ];
    for (const s of selectors) {
        const el = document.querySelector(s);
        if (el) { 
            console.log('Clicking consent button:', s);
            el.click(); 
            return; 
        }
    }
})();
"""


class Crawl4AITracker:
    """Thread-safe tracker for concurrent crawl4ai requests; logs concurrency metrics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self._peak = 0
        self._total = 0
        self._log = logger.bind(component="Crawl4AITracker")

    def start(self, url: str) -> float:
        """Record the start of a crawl4ai request and return start time."""
        with self._lock:
            self._active += 1
            self._total += 1
            self._peak = max(self._peak, self._active)
            active = self._active
            peak = self._peak
            total = self._total
        self._log.debug(
            "crawl4ai_start",
            active=active,
            peak=peak,
            total=total,
            url_preview=url[:80],
        )
        return time.time()

    def end(self, url: str, start_time: float, success: bool) -> None:
        """Record the end of a crawl4ai request."""
        duration = time.time() - start_time
        with self._lock:
            self._active -= 1
            active = self._active
        self._log.debug(
            "crawl4ai_end",
            success=success,
            active=active,
            duration_s=round(duration, 2),
            url_preview=url[:80],
        )


_tracker = Crawl4AITracker()
_SCRAPER_LOG = logger.bind(component="Crawl4AIScraper")


def _page_text_fallback(crawl_result: Any) -> str:
    """Use crawl markdown/HTML when LLM extraction yields nothing usable."""
    md = getattr(crawl_result, "_markdown", None)
    if md is not None:
        for attr in ("fit_markdown", "raw_markdown", "markdown_with_citations"):
            text = getattr(md, attr, None)
            if isinstance(text, str) and text.strip():
                return text.strip()
    for attr in ("cleaned_html", "fit_html", "html"):
        text = getattr(crawl_result, attr, None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


async def extract_structured_data(
    url: str,
    schema: Type[BaseModel],
    model_name: str = "mistral-nemo-instruct-2407-abliterated",
    instruction: str = "Extract the data according to the schema.",
) -> Optional[Dict[str, Any]]:
    """
    Scrapes a URL using Crawl4AI with local LLM extraction (LM Studio).
    Handles consent popups via 'Magic Mode' and fallback JS.
    """
    # Track request start
    start_time = _tracker.start(url)

    _SCRAPER_LOG.info("crawl4ai_step_llm_config", model_name=model_name)

    # 1. Configure LLM (Using 'openai/' provider allows generic local URL usage)
    llm_config = LLMConfig(
        provider=f"openai/{model_name}",
        base_url=LM_STUDIO_URL,
        api_token=API_TOKEN,
        temperature=0,
    )

    # 2. Define Extraction Strategy
    strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=schema.model_json_schema(),
        instruction=instruction,
        input_format="fit_markdown",  # Best for local LLMs (reduces noise)
        verbose=False,
    )

    # 3. Configure Crawler Run (Magic Mode + Anti-Overlay)
    run_config = CrawlerRunConfig(
        extraction_strategy=strategy,
        cache_mode=CacheMode.BYPASS,  # Ensure fresh data
        magic=True,  # AUTO: Handle popups/user-simulation
        remove_overlay_elements=True,  # AUTO: Strip modals/banners
        js_code=JS_CONSENT_CLICK,  # MANUAL: Fallback click script
        wait_for="body",  # Wait for page load
        page_timeout=60000,  # 60s page load timeout
    )

    _SCRAPER_LOG.info("crawl4ai_step_visit", url=url)

    try:
        # verbose=False: crawl4ai logs use Unicode symbols that break on Windows cp1252 consoles.
        async with AsyncWebCrawler(verbose=False) as crawler:
            # Run the crawl
            result: Any = await crawler.arun(url=url, config=run_config)

            if not result.success:
                _SCRAPER_LOG.error(
                    "crawl4ai_crawl_failed",
                    url=url,
                    error_message=result.error_message,
                )
                _tracker.end(url, start_time, success=False)
                return None

            _SCRAPER_LOG.info("crawl4ai_step_parse", url=url)

            # 4. Parse and Return Data
            try:
                extracted = result.extracted_content
                if not (isinstance(extracted, str) and extracted.strip()):
                    fb = _page_text_fallback(result)
                    if fb:
                        _SCRAPER_LOG.warning(
                            "crawl4ai_fallback_markdown",
                            reason="no_llm_extracted_content",
                            url=url,
                        )
                        _tracker.end(url, start_time, success=True)
                        return {"raw_content": fb}
                    _tracker.end(url, start_time, success=False)
                    return None
                # result.extracted_content is usually a JSON string from the LLM
                data = json.loads(extracted)
                if data is None or (isinstance(data, list) and len(data) == 0):
                    fb = _page_text_fallback(result)
                    if fb:
                        _SCRAPER_LOG.warning(
                            "crawl4ai_fallback_markdown",
                            reason="llm_json_empty",
                            url=url,
                        )
                        _tracker.end(url, start_time, success=True)
                        return {"raw_content": fb}
                    _tracker.end(url, start_time, success=False)
                    return None
                _SCRAPER_LOG.info("crawl4ai_success", url=url)
                _tracker.end(url, start_time, success=True)
                return cast(Dict[str, Any], data)
            except json.JSONDecodeError:
                _SCRAPER_LOG.warning(
                    "crawl4ai_llm_raw_text_not_json",
                    url=url,
                )
                raw = result.extracted_content
                if isinstance(raw, str) and raw.strip():
                    _tracker.end(url, start_time, success=True)
                    return {"raw_content": raw}
                fb = _page_text_fallback(result)
                if fb:
                    _tracker.end(url, start_time, success=True)
                    return {"raw_content": fb}
                _tracker.end(url, start_time, success=False)
                return None
            except Exception as e:
                _SCRAPER_LOG.exception("crawl4ai_content_processing_error", url=url)
                fb = _page_text_fallback(result)
                if fb:
                    _SCRAPER_LOG.warning(
                        "crawl4ai_fallback_markdown",
                        reason="after_parse_error",
                        url=url,
                        error=str(e),
                    )
                    _tracker.end(url, start_time, success=True)
                    return {"raw_content": fb}
                _tracker.end(url, start_time, success=False)
                return None
    except Exception:
        _SCRAPER_LOG.exception("crawl4ai_unexpected_error", url=url)
        _tracker.end(url, start_time, success=False)
        raise
