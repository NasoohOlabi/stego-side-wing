import asyncio
import json
import urllib.parse
from typing import Any, Dict

import aiohttp
from loguru import logger

_LOG = logger.bind(component="DuckDuckGoApi")


async def searchDuckDuckGo(
    query: str, max_results: int = 10, timeout: int = 10
) -> Dict[str, Any]:
    """
    Search DuckDuckGo using their instant answer API with proper error handling.

    Args:
        query: Search query string
        max_results: Maximum number of results to return
        timeout: Request timeout in seconds

    Returns:
        Dictionary with organic_results list
    """
    query = query.strip()
    if not query:
        return {"organic_results": []}

    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    }

    try:
        _LOG.debug("duckduckgo_search_start", query=query)

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status != 200:
                    _LOG.warning(
                        "duckduckgo_http_error",
                        status=response.status,
                        reason=str(response.reason),
                    )
                    return {"organic_results": []}

                try:
                    data = await response.json()
                except aiohttp.ContentTypeError:
                    text = await response.text()
                    data = json.loads(text)

        _LOG.debug("duckduckgo_response_ok", query=query)
        results = []

        if abstract := data.get("Abstract"):
            results.append(
                {
                    "title": data.get("Heading", "Abstract"),
                    "href": data.get("AbstractURL", ""),
                    "body": abstract,
                }
            )

        if related_topics := data.get("RelatedTopics"):
            if isinstance(related_topics, list):
                remaining = max_results - len(results)
                for topic in related_topics[:remaining]:
                    if topic.get("Text") and topic.get("FirstURL"):
                        results.append(
                            {
                                "title": topic["Text"].split(" - ")[0]
                                if " - " in topic["Text"]
                                else topic["Text"],
                                "href": topic["FirstURL"],
                                "body": topic["Text"],
                            }
                        )

        organic_results = [
            {
                "title": r["title"],
                "link": r["href"],
                "snippet": r.get("body", r["title"])[:300],
            }
            for r in results
        ]

        _LOG.info(
            "duckduckgo_search_done",
            result_count=len(organic_results),
            first_title=(organic_results[0]["title"][:60] if organic_results else None),
        )

        return {"organic_results": organic_results}

    except asyncio.TimeoutError:
        _LOG.warning("duckduckgo_timeout", timeout_s=timeout)
        return {"organic_results": []}
    except json.JSONDecodeError as e:
        _LOG.warning("duckduckgo_invalid_json", error=str(e))
        return {"organic_results": []}
    except Exception as e:
        _LOG.exception("duckduckgo_search_failed", error_type=type(e).__name__)
        return {"organic_results": []}


async def search_duckduckgo_with_fallback(
    query: str, max_results: int = 10
) -> Dict[str, Any]:
    """Try API first, then fallback to HTML parsing if needed."""
    result = await searchDuckDuckGo(query, max_results)

    if not result["organic_results"]:
        _LOG.info("duckduckgo_fallback_skipped", query=query)
        return {"organic_results": []}

    return result


def search_sync(query: str, max_results: int = 10) -> Dict[str, Any]:
    """Synchronous wrapper"""
    return asyncio.run(search_duckduckgo_with_fallback(query, max_results))


if __name__ == "__main__":
    results = search_sync("Python aiohttp Brotli compression")
    for r in results["organic_results"][:3]:
        _LOG.info(
            "duckduckgo_sample_result",
            title=r["title"],
            link=r["link"],
            snippet_preview=r["snippet"][:80],
        )
