"""Backward compatibility shims for util/* imports."""

# Re-export from integrations for backward compatibility
# DuckDuckGo API - note: keeping original casing for compatibility
from integrations.duckduckgo_api import (
    search_duckduckgo_with_fallback,
    search_sync,
    searchDuckDuckGo,
)
from integrations.news_api import (
    Article,
    ArticleSource,
    EverythingParams,
    NewsApiErrorResponse,
    NewsApiResponse,
    NewsApiSuccessResponse,
    fetch_everything,
)

# ScrapingDog API
from integrations.scrapingdog_api import searchGoogle

__all__ = [
    # News API
    "Article",
    "ArticleSource",
    "EverythingParams",
    "NewsApiErrorResponse",
    "NewsApiResponse",
    "NewsApiSuccessResponse",
    "fetch_everything",
    # DuckDuckGo
    "searchDuckDuckGo",
    # ScrapingDog
    "searchGoogle",
    "search_duckduckgo_with_fallback",
    "search_sync",
]
