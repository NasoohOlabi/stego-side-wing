import asyncio
import json
import os
import threading
import time
from typing import Any, Dict, Optional, Type

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from pydantic import BaseModel

# --- Configuration Constants ---
LM_STUDIO_URL = "http://192.168.100.136:1234/v1"

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
        print(f"🚀 CRAWL4AI START | Active: {active} | Peak: {peak} | Total: {total} | URL: {url[:80]}")
        return time.time()
    
    def end(self, url: str, start_time: float, success: bool) -> None:
        """Record the end of a crawl4ai request."""
        duration = time.time() - start_time
        status = "✅" if success else "❌"
        with self._lock:
            self._active -= 1
            active = self._active
        print(f"{status} CRAWL4AI END   | Active: {active} | Duration: {duration:.2f}s | URL: {url[:80]}")


# Global tracker instance
_tracker = Crawl4AITracker()


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
        verbose=True,
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
        async with AsyncWebCrawler(verbose=True) as crawler:
            # Run the crawl
            result = await crawler.arun(url=url, config=run_config)

            if not result.success:
                print(f"[ERROR] Crawl Failed: {result.error_message}")
                _tracker.end(url, start_time, success=False)
                return None

            print("[3/4] Extraction complete. Parsing results...")

            # 4. Parse and Return Data
            try:
                # result.extracted_content is usually a JSON string from the LLM
                data = json.loads(result.extracted_content)
                print("[4/4] Success!")
                _tracker.end(url, start_time, success=True)
                return data
            except json.JSONDecodeError:
                print(
                    "[WARNING] LLM returned raw text, not valid JSON. Returning raw content."
                )
                _tracker.end(url, start_time, success=True)
                return {"raw_content": result.extracted_content}
            except Exception as e:
                print(f"[ERROR] processing content: {e}")
                _tracker.end(url, start_time, success=False)
                return None
    except Exception as e:
        print(f"[ERROR] Unexpected error during crawl: {e}")
        _tracker.end(url, start_time, success=False)
        raise
