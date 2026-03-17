"""Search service for external search APIs."""
import os
from typing import Any, Dict, List

import dotenv
import ollama
import requests

from infrastructure.config import get_env, get_env_required


def search_news_api(query: str) -> Dict[str, Any]:
    """
    Search using News API (deprecated endpoint logic).
    
    Args:
        query: Search query
        
    Returns:
        Dict with 'results' list or error info
    """
    from integrations.news_api import (
        EverythingParams,
        NewsApiErrorResponse,
        NewsApiSuccessResponse,
        fetch_everything,
    )
    from typing import cast

    search_params: EverythingParams = {
        "q": query,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 5,
    }

    print(f"Fetching news for: {search_params['q']}")
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


def search_ollama(query: str) -> List[Dict[str, str]]:
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
    
    print(f"Fetching from Ollama: {query}")
    response = client.web_search(query)
    return [
        {"title": x.title or "", "url": x.url or "", "content": x.content or ""}
        for x in response.results
    ]


def search_bing(query: str, first: int = 1, count: int = 10) -> Dict[str, List[Dict[str, str]]]:
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

    try:
        resp = requests.get(
            "https://api.scrapingdog.com/bing/search", params=params, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": [
                {"title": x["title"], "link": x["link"], "snippet": x["snippet"]}
                for x in data["bing_data"]
            ]
        }
    except requests.RequestException as e:
        raise ValueError(f"Bing search failed: {str(e)}")


def search_google(query: str, first: int = 1, count: int = 10) -> Dict[str, List[Dict[str, str]]]:
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
            continue

    raise ValueError(
        f"All {len(api_keys)} Google keys failed to fetch results. Errors: {errors}"
    )
