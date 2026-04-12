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

from infrastructure.config import (
    get_env,
    get_lm_studio_url,
    get_workflow_llm_backend,
    resolve_workflow_llm_provider_and_model,
)
from workflows.adapters.llm import LLMAdapter


def _lm_studio_api_token() -> str:
    return (get_env("LM_STUDIO_API_TOKEN") or "lm-studio").strip() or "lm-studio"

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


def _markdown_only_run_config() -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=True,
        remove_overlay_elements=True,
        js_code=JS_CONSENT_CLICK,
        wait_for="body",
        page_timeout=60000,
    )


def parse_structured_llm_schema_text(
    raw: str, schema: Type[BaseModel]
) -> Optional[Dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        chunks = text.split("```")
        if len(chunks) >= 2:
            inner = chunks[1].strip()
            if inner.lower().startswith("json"):
                inner = inner[4:].lstrip()
            text = inner.strip()
    try:
        validated = schema.model_validate_json(text)
        return validated.model_dump()
    except Exception:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                validated = schema.model_validate(parsed)
                return validated.model_dump()
        except Exception:
            return None
        return None


async def _extract_structured_google_backend(
    url: str,
    schema: Type[BaseModel],
    model_name: str,
    instruction: str,
    start_time: float,
) -> Optional[Dict[str, Any]]:
    _SCRAPER_LOG.info(
        "crawl4ai_step_google_backend", url=url, model_name=model_name
    )
    run_config = _markdown_only_run_config()
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
            result: Any = await crawler.arun(url=url, config=run_config)
    except Exception:
        _SCRAPER_LOG.exception("crawl4ai_unexpected_error", url=url)
        _tracker.end(url, start_time, success=False)
        raise

    if not result.success:
        _SCRAPER_LOG.error(
            "crawl4ai_crawl_failed",
            url=url,
            error_message=result.error_message,
        )
        _tracker.end(url, start_time, success=False)
        return None

    page_text = _page_text_fallback(result)
    if not page_text.strip():
        _tracker.end(url, start_time, success=False)
        return None

    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    prompt = (
        f"{instruction}\n\n"
        "Output a single JSON object only (no markdown fences) that conforms "
        f"to this JSON Schema:\n{schema_json}\n\nPage content:\n{page_text[:120_000]}"
    )
    provider, model = resolve_workflow_llm_provider_and_model(model_name)
    raw = LLMAdapter().call_llm(
        prompt=prompt,
        system_message="You extract structured data. Reply with JSON only, no prose.",
        model=model,
        provider=provider,
        temperature=0.0,
        max_tokens=None,
    )
    data = parse_structured_llm_schema_text(raw, schema)
    if data is not None:
        _SCRAPER_LOG.info("crawl4ai_success", url=url)
        _tracker.end(url, start_time, success=True)
        return data
    if raw.strip():
        _tracker.end(url, start_time, success=True)
        return {"raw_content": raw}
    _tracker.end(url, start_time, success=False)
    return None


async def _extract_structured_lm_studio_backend(
    url: str,
    schema: Type[BaseModel],
    model_name: str,
    instruction: str,
    start_time: float,
) -> Optional[Dict[str, Any]]:
    base_url = get_lm_studio_url()
    llm_config = LLMConfig(
        provider=f"openai/{model_name}",
        base_url=base_url,
        api_token=_lm_studio_api_token(),
        temperature=0,
    )
    strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=schema.model_json_schema(),
        instruction=instruction,
        input_format="fit_markdown",
        verbose=False,
    )
    run_config = CrawlerRunConfig(
        extraction_strategy=strategy,
        cache_mode=CacheMode.BYPASS,
        magic=True,
        remove_overlay_elements=True,
        js_code=JS_CONSENT_CLICK,
        wait_for="body",
        page_timeout=60000,
    )
    _SCRAPER_LOG.info("crawl4ai_step_visit", url=url)
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
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
                raw_err = result.extracted_content
                if isinstance(raw_err, str) and raw_err.strip():
                    _tracker.end(url, start_time, success=True)
                    return {"raw_content": raw_err}
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


async def extract_structured_data(
    url: str,
    schema: Type[BaseModel],
    model_name: str = "mistral-nemo-instruct-2407-abliterated",
    instruction: str = "Extract the data according to the schema.",
) -> Optional[Dict[str, Any]]:
    """
    Crawl4AI page fetch; structured fields via LLM.

    ``WORKFLOW_LLM_BACKEND=lm_studio``: OpenAI-compatible server at ``LM_STUDIO_URL``.
    ``ai_studio`` / ``google`` / ``gemini``: markdown crawl then ``LLMAdapter`` (Gemini).
    """
    start_time = _tracker.start(url)
    _SCRAPER_LOG.info("crawl4ai_step_llm_config", model_name=model_name)
    if get_workflow_llm_backend() == "google":
        return await _extract_structured_google_backend(
            url, schema, model_name, instruction, start_time
        )
    return await _extract_structured_lm_studio_backend(
        url, schema, model_name, instruction, start_time
    )
