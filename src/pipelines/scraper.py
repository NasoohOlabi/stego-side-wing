import asyncio
import json
import os
import threading
import time
from typing import Any, Dict, Optional, Type

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy
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
    """Thread-safe tracker for concurrent crawl4ai requests."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._active = 0
        self._peak = 0
        self._total = 0
    
    def start(self, url: str) -> float:
        """Record the start of a crawl4ai request and return start time."""
        with self._lock:
            self._active += 1
            self._total += 1
            self._peak = max(self._peak, self._active)
            active = self._active
            peak = self._peak
            total = self._total
        print(f"CRAWL4AI START | Active: {active} | Peak: {peak} | Total: {total} | URL: {url[:80]}")
        return time.time()
    
    def end(self, url: str, start_time: float, success: bool) -> None:
        """Record the end of a crawl4ai request."""
        duration = time.time() - start_time
        status = "OK" if success else "FAIL"
        with self._lock:
            self._active -= 1
            active = self._active
        print(f"{status} CRAWL4AI END   | Active: {active} | Duration: {duration:.2f}s | URL: {url[:80]}")


# Global tracker instance
_tracker = Crawl4AITracker()


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

    print(f"\n[1/4] Initializing LLM Config for: {model_name}")

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

    print(f"[2/4] Visiting URL: {url}")

    try:
        # verbose=False: crawl4ai logs use Unicode symbols that break on Windows cp1252 consoles.
        async with AsyncWebCrawler(verbose=False) as crawler:
            # Run the crawl
            result: Any = await crawler.arun(url=url, config=run_config)

            if not result.success:
                print(f"[ERROR] Crawl Failed: {result.error_message}")
                _tracker.end(url, start_time, success=False)
                return None

            print("[3/4] Extraction complete. Parsing results...")

            # 4. Parse and Return Data
            try:
                extracted = result.extracted_content
                if not (isinstance(extracted, str) and extracted.strip()):
                    fb = _page_text_fallback(result)
                    if fb:
                        print(
                            "[WARNING] No LLM extracted_content; using page markdown/HTML fallback."
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
                        print(
                            "[WARNING] LLM JSON empty; using page markdown/HTML fallback."
                        )
                        _tracker.end(url, start_time, success=True)
                        return {"raw_content": fb}
                    _tracker.end(url, start_time, success=False)
                    return None
                print("[4/4] Success!")
                _tracker.end(url, start_time, success=True)
                return data
            except json.JSONDecodeError:
                print(
                    "[WARNING] LLM returned raw text, not valid JSON. Returning raw content."
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
                print(f"[ERROR] processing content: {e}")
                fb = _page_text_fallback(result)
                if fb:
                    print("[WARNING] Using page markdown/HTML fallback after parse error.")
                    _tracker.end(url, start_time, success=True)
                    return {"raw_content": fb}
                _tracker.end(url, start_time, success=False)
                return None
    except Exception as e:
        print(f"[ERROR] Unexpected error during crawl: {e}")
        _tracker.end(url, start_time, success=False)
        raise
