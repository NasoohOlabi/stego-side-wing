import asyncio
import json
import urllib.parse
from typing import Any, Dict, List, Optional

import aiohttp
from icecream import ic


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
    # Clean and encode query
    query = query.strip()
    if not query:
        return {"organic_results": []}

    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"

    # Headers without Brotli compression
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",  # Removed 'br' to avoid Brotli issues
    }

    try:
        print(f'🔍 Searching DuckDuckGo: "{query}"')

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                # Check status
                if response.status != 200:
                    print(f"⚠️  HTTP {response.status}: {response.reason}")
                    return {"organic_results": []}

                # Parse JSON
                try:
                    data = await response.json()
                except aiohttp.ContentTypeError:
                    # Fallback to text parsing
                    text = await response.text()
                    data = json.loads(text)

        print("✅ DuckDuckGo API response received")
        ic(data)
        # Transform results
        results = []

        # Add abstract if available
        if abstract := data.get("Abstract"):
            results.append(
                {
                    "title": data.get("Heading", "Abstract"),
                    "href": data.get("AbstractURL", ""),
                    "body": abstract,
                }
            )

        # Add related topics
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

        # Format final results
        organic_results = [
            {
                "title": r["title"],
                "link": r["href"],
                "snippet": r.get("body", r["title"])[:300],  # Limit snippet length
            }
            for r in results
        ]

        print(f"📊 Found {len(organic_results)} results")
        if organic_results:
            print(f"📊 First result: {organic_results[0]['title'][:60]}...")

        return {"organic_results": organic_results}

    except asyncio.TimeoutError:
        print(f"⏰ Search timed out after {timeout}s")
        return {"organic_results": []}
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response: {e}")
        return {"organic_results": []}
    except Exception as e:
        print(f"❌ Search failed: {type(e).__name__}: {e}")
        return {"organic_results": []}


async def search_duckduckgo_with_fallback(
    query: str, max_results: int = 10
) -> Dict[str, Any]:
    """
    Try API first, then fallback to HTML parsing if needed.
    """
    # Try API method
    result = await searchDuckDuckGo(query, max_results)

    # If no results, try HTML fallback
    if not result["organic_results"]:
        print("🔄 No results from API, trying HTML fallback...")
        # Your existing searchDuckDuckGoHTML function would go here
        # For now, returns empty
        return {"organic_results": []}

    return result


def search_sync(query: str, max_results: int = 10) -> Dict[str, Any]:
    """Synchronous wrapper"""
    return asyncio.run(search_duckduckgo_with_fallback(query, max_results))


# Example usage
if __name__ == "__main__":
    # Test search
    results = search_sync("Python aiohttp Brotli compression")
    for r in results["organic_results"][:3]:
        print(f"• {r['title']}\n  {r['link']}\n  {r['snippet'][:80]}...\n")
