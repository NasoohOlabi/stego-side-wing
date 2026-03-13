"""Backward compatibility shims for util/* imports."""

# Re-export from integrations for backward compatibility
from integrations.news_api import (
    Article,
    ArticleSource,
    EverythingParams,
    NewsApiErrorResponse,
    NewsApiSuccessResponse,
    NewsApiResponse,
    fetch_everything,
)

# DuckDuckGo API - note: keeping original casing for compatibility
from integrations.duckduckgo_api import (
    searchDuckDuckGo,
    search_duckduckgo_with_fallback,
    search_sync,
)

# ScrapingDog API
from integrations.scrapingdog_api import searchGoogle

# Lumen API
from integrations.lumen_api import (
    Article as LumenArticle,
    ArticlesResponse,
    Meta as LumenMeta,
)

__all__ = [
    # News API
    "Article",
    "ArticleSource",
    "EverythingParams",
    "NewsApiErrorResponse",
    "NewsApiSuccessResponse",
    "NewsApiResponse",
    "fetch_everything",
    # DuckDuckGo
    "searchDuckDuckGo",
    "search_duckduckgo_with_fallback",
    "search_sync",
    # ScrapingDog
    "searchGoogle",
    # Lumen
    "LumenArticle",
    "ArticlesResponse",
    "LumenMeta",
]
