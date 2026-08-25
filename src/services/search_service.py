"""Search service for external search APIs."""

import html
import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree

import ollama
import requests
from bs4 import BeautifulSoup

from infrastructure.config import get_env, get_env_required

logger = logging.getLogger(__name__)


def _plain_rss_text(value: str) -> str:
    """Collapse the small HTML fragments commonly embedded in RSS fields."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _bing_rss_source_url(value: str) -> str:
    """Prefer Bing's encoded publisher URL over its tracking redirect."""
    query = parse_qs(urlparse(value).query)
    source_urls = query.get("url")
    return source_urls[0] if source_urls else value


def _bing_rss_items(content: bytes, count: int) -> list[dict[str, str]]:
    root = ElementTree.fromstring(content)
    items: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[: max(1, count)]:
        link = _bing_rss_source_url(item.findtext("link", default=""))
        if link:
            items.append(
                {
                    "title": _plain_rss_text(item.findtext("title", default="")),
                    "link": link,
                    "snippet": _plain_rss_text(item.findtext("description", default="")),
                }
            )
    return items


def _google_news_rss_items(content: bytes, count: int) -> list[dict[str, str]]:
    root = ElementTree.fromstring(content)
    results: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:count]:
        link = item.findtext("link", default="")
        if not link:
            continue
        results.append(
            {
                "title": _plain_rss_text(item.findtext("title", default="")),
                "link": f"https://r.jina.ai/{link}",
                "snippet": _plain_rss_text(item.findtext("description", default="")),
            }
        )
    return results


def _yahoo_news_items(content: str, count: int) -> list[dict[str, str]]:
    soup = BeautifulSoup(content, "html.parser")
    results: list[dict[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        match = re.search(r"/RU=([^/]+)", href)
        title = anchor.get_text(" ", strip=True)
        if not match or len(title) < 20 or "/RV=2/" not in href:
            continue
        link = unquote(match.group(1))
        if not link.startswith(("http://", "https://")):
            continue
        if urlparse(link).hostname == "guce.yahoo.com":
            continue
        results.append(
            {
                "title": title,
                "link": link,
                "snippet": anchor.parent.get_text(" ", strip=True) if anchor.parent else title,
            }
        )
        if len(results) >= count:
            break
    return results


def search_news_api(query: str) -> dict[str, Any]:
    """
    Search using News API (deprecated endpoint logic).

    Args:
        query: Search query

    Returns:
        Dict with 'results' list or error info
    """
    from typing import cast

    from integrations.news_api import (
        EverythingParams,
        NewsApiErrorResponse,
        NewsApiSuccessResponse,
        fetch_everything,
    )

    search_params: EverythingParams = {
        "q": query,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 5,
    }

    logger.info(
        "search_news_api",
        extra={"event": "search", "provider": "news_api", "query": query},
    )
    try:
        result = fetch_everything(search_params)

        if result["status"] == "ok":
            success_result = cast(NewsApiSuccessResponse, result)
            return {
                "results": [
                    {
                        "title": x["title"],
                        "link": x["url"],
                        "snippet": x["description"],
                    }
                    for x in success_result["articles"]
                ]
            }
        else:
            error_result = cast(NewsApiErrorResponse, result)
            return {"error": error_result}
    except Exception as e:
        return {"error": str(e)}


def search_ollama(query: str) -> list[dict[str, str]]:
    """
    Search using Ollama web search.

    Args:
        query: Search query

    Returns:
        List of search results with title, url, content

    Raises:
        ValueError: If query is missing
    """
    if not query:
        raise ValueError("No query")

    ollama_api_key = get_env_required("OLLAMA_API_KEY")
    client = ollama.Client(
        host="https://ollama.com", headers={"Authorization": "Bearer " + ollama_api_key}
    )

    logger.info(
        "search_ollama",
        extra={"event": "search", "provider": "ollama", "query": query},
    )
    response = client.web_search(query)
    return [
        {"title": x.title or "", "url": x.url or "", "content": x.content or ""}
        for x in response.results
    ]


def search_duckduckgo(
    query: str, first: int = 1, count: int = 10
) -> dict[str, list[dict[str, str]]]:
    """Search DuckDuckGo and normalize to the Google/Bing ``{results: [...]}`` shape."""
    if not query:
        raise ValueError("Missing 'query' parameter")
    from integrations.duckduckgo_api import search_sync

    logger.info(
        "search_duckduckgo",
        extra={
            "event": "search",
            "provider": "duckduckgo",
            "query": query,
            "first": first,
            "count": count,
        },
    )
    raw = search_sync(query, max_results=max(1, count))
    organic = raw.get("organic_results")
    if not isinstance(organic, list):
        return {"results": []}
    start = max(0, int(first) - 1)
    sliced = organic[start : start + max(1, count)]
    return {
        "results": [
            {
                "title": str(item.get("title") or ""),
                "link": str(item.get("link") or ""),
                "snippet": str(item.get("snippet") or ""),
            }
            for item in sliced
            if isinstance(item, dict)
        ]
    }


def search_bing_news_rss(
    query: str, first: int = 1, count: int = 10
) -> dict[str, list[dict[str, str]]]:
    """Search Bing News RSS without an API key or metered search quota."""
    if not query:
        raise ValueError("Missing 'query' parameter")
    logger.info(
        "search_bing_news_rss",
        extra={"event": "search", "provider": "bing_news_rss", "query": query},
    )
    response = requests.get(
        "https://www.bing.com/news/search",
        params={"q": query, "format": "rss"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    results = _bing_rss_items(response.content, max(1, first - 1) + count)
    start = max(0, first - 1)
    return {"results": results[start : start + max(1, count)]}


def search_google_news_rss(
    query: str, first: int = 1, count: int = 10
) -> dict[str, list[dict[str, str]]]:
    """Search Google News RSS and resolve its links to publisher URLs."""
    if not query:
        raise ValueError("Missing 'query' parameter")
    logger.info(
        "search_google_news_rss",
        extra={"event": "search", "provider": "google_news_rss", "query": query},
    )
    response = requests.get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    needed = max(1, first - 1) + max(1, count)
    results = _google_news_rss_items(response.content, needed)
    start = max(0, first - 1)
    return {"results": results[start : start + max(1, count)]}


def search_yahoo_news(
    query: str, first: int = 1, count: int = 10
) -> dict[str, list[dict[str, str]]]:
    """Search Yahoo News HTML and resolve its redirect links to publishers."""
    if not query:
        raise ValueError("Missing 'query' parameter")
    logger.info(
        "search_yahoo_news",
        extra={"event": "search", "provider": "yahoo_news", "query": query},
    )
    response = requests.get(
        "https://news.search.yahoo.com/search",
        params={"p": query},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    needed = max(1, first - 1) + max(1, count)
    results = _yahoo_news_items(response.text, needed)
    start = max(0, first - 1)
    return {"results": results[start : start + max(1, count)]}


def search_bing_web_rss(
    query: str, first: int = 1, count: int = 10
) -> dict[str, list[dict[str, str]]]:
    """Search Bing's general Web RSS feed without an API key."""
    if not query:
        raise ValueError("Missing 'query' parameter")
    logger.info(
        "search_bing_web_rss",
        extra={"event": "search", "provider": "bing_web_rss", "query": query},
    )
    response = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()
    results = _bing_rss_items(response.content, max(1, first - 1) + count)
    start = max(0, first - 1)
    return {"results": results[start : start + max(1, count)]}


def search_bing(query: str, first: int = 1, count: int = 10) -> dict[str, list[dict[str, str]]]:
    """
    Search using Bing via ScrapingDog API.

    Args:
        query: Search query
        first: Starting index
        count: Number of results

    Returns:
        Dict with 'results' list

    Raises:
        ValueError: If query is missing or API key not configured
    """
    if not query:
        raise ValueError("Missing 'query' parameter")

    api_key = get_env("SCRAPINGDOG_API_KEY")
    if not api_key:
        raise ValueError("ScrapingDog API key not configured")

    params = {"query": query, "first": first, "count": count, "api_key": api_key}

    logger.info(
        "search_bing",
        extra={
            "event": "search",
            "provider": "bing",
            "query": query,
            "first": first,
            "count": count,
        },
    )
    try:
        resp = requests.get("https://api.scrapingdog.com/bing/search", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": [
                {"title": x["title"], "link": x["link"], "snippet": x["snippet"]}
                for x in data["bing_data"]
            ]
        }
    except requests.RequestException as e:
        raise ValueError(f"Bing search failed: {e!s}") from e


def search_google(query: str, first: int = 1, count: int = 10) -> dict[str, list[dict[str, str]]]:
    """
    Search using Google Custom Search API.

    Args:
        query: Search query
        first: Starting index
        count: Number of results

    Returns:
        Dict with 'results' list

    Raises:
        ValueError: If query is missing or API keys not configured
    """
    if not query:
        raise ValueError("Missing 'query' parameter")

    cse_id = get_env("GOOGLE_CSE_ID")
    if not cse_id:
        raise ValueError("Google Custom Search Engine ID not configured")

    # Retrieve API keys from environment or dotenv (try GOOGLE_API_KEY_1 through GOOGLE_API_KEY_5)
    api_keys = []
    for i in range(1, 6):
        key_name = f"GOOGLE_API_KEY_{i}"
        api_key = get_env(key_name)
        if api_key:
            api_keys.append(api_key)

    if not api_keys:
        raise ValueError("No Google API keys configured")

    logger.info(
        "search_google",
        extra={
            "event": "search",
            "provider": "google",
            "query": query,
            "first": first,
            "count": count,
        },
    )

    # Try each API key sequentially until one succeeds
    errors = []
    for idx, api_key in enumerate(api_keys, 1):
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": count,
            "start": first,
        }
        try:
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1", params=params, timeout=20
            )
            resp.raise_for_status()
            data = resp.json()

            # Check for error in JSON response
            if "error" in data:
                error_info = data.get("error", {})
                error_message = error_info.get("message", "Unknown error")
                errors.append(
                    {
                        "key_index": idx,
                        "error": f"API error: {error_message}",
                        "data": data,
                    }
                )
                logger.warning(
                    "search_google_key_failed",
                    extra={
                        "event": "search",
                        "provider": "google",
                        "query": query,
                        "key_index": idx,
                        "reason": "api_error_json",
                        "detail": error_message[:500],
                    },
                )
                continue

            # Check if "items" key exists
            if "items" not in data:
                return {"results": []}

            # Success - return the results
            return {
                "results": [
                    {
                        "title": x.get("title", ""),
                        "link": x.get("link", ""),
                        "snippet": x.get("snippet", ""),
                    }
                    for x in data["items"]
                ]
            }
        except requests.RequestException as e:
            errors.append(
                {
                    "key_index": idx,
                    "error": str(e),
                    "data": None,
                }
            )
            logger.warning(
                "search_google_key_failed",
                extra={
                    "event": "search",
                    "provider": "google",
                    "query": query,
                    "key_index": idx,
                    "reason": "request_exception",
                    "detail": str(e)[:500],
                },
            )
            continue

    raise ValueError(f"All {len(api_keys)} Google keys failed to fetch results. Errors: {errors}")
