import json
import os
import sys  # Added import for sys.stderr
from typing import Union  # Added import for cast
from typing import List, Optional, TypedDict, cast
from urllib.parse import urlencode

import dotenv
import requests
from icecream import ic

# --- 1. Response Type Definitions (TypedDicts) ---


# Defines the structure for the source object within an Article.
class ArticleSource(TypedDict):
    id: Optional[str]
    name: str


# Defines the structure for a single news article returned by the API.
class Article(TypedDict):
    source: ArticleSource
    author: Optional[str]
    title: str
    description: Optional[str]
    url: str
    urlToImage: Optional[str]
    publishedAt: str  # ISO 8601 date string
    content: Optional[str]


# Defines the overall successful response structure for the /everything endpoint.
class NewsApiSuccessResponse(TypedDict):
    status: str  # Should be 'ok'
    totalResults: int
    articles: List[Article]


# Defines the structure for an error response from the API.
class NewsApiErrorResponse(TypedDict):
    status: str  # Should be 'error'
    code: str
    message: str


# The union of possible API responses.
NewsApiResponse = Union[NewsApiSuccessResponse, NewsApiErrorResponse]

# --- 2. Request Parameters Type Definition (TypedDict) ---


# Defines the typed parameters for the News API /v2/everything endpoint.
class EverythingParams(TypedDict, total=False):
    q: str  # Mandatory: Keywords or phrases to search for.
    searchIn: str  # 'title', 'description', 'content'
    sources: str  # Comma-separated list of sources
    domains: str  # Comma-separated list of domains
    excludeDomains: str
    from_date: str  # Use 'from_date' in Python to avoid collision with 'from' keyword
    to: str
    language: str  # 'ar', 'en', 'es', etc.
    sortBy: str  # 'relevancy', 'popularity', 'publishedAt'
    pageSize: int
    page: int


# --- 3. API Function and Constants ---

# IMPORTANT: Replace this placeholder with your actual News API key.
dotenv.load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
BASE_URL = "https://newsapi.org/v2/everything"


def fetch_everything(params: EverythingParams) -> NewsApiResponse:
    """
    Fetches news articles using the News API /v2/everything endpoint.

    Args:
        params: The typed parameters for the API call.

    Returns:
        A dictionary matching the NewsApiResponse TypedDict structure.
    """
    if NEWS_API_KEY == "YOUR_NEWS_API_KEY":
        print(
            "ERROR: Please replace 'YOUR_NEWS_API_KEY' with your actual key.",
            file=sys.stderr,
        )
        # Return a structured error response for type safety
        return NewsApiErrorResponse(
            status="error",
            code="apiKeyMissing",
            message="News API key is not configured.",
        )

    # Prepare parameters for the URL
    query_params = {
        "apiKey": NEWS_API_KEY,
        # 'from' is a reserved keyword in Python, so the caller should use 'from_date'
        **{k if k != "from_date" else "from": v for k, v in params.items()},
    }
    ic(query_params)
    # Encode parameters and construct the final URL
    url = f"{BASE_URL}?{urlencode(query_params)}"
    ic(url)
    try:
        # Make the synchronous HTTP GET request
        response = requests.get(url)

        # Raise an exception for HTTP error codes (4xx or 5xx)
        response.raise_for_status()

        # Parse the JSON response. Pylance treats this initially as a generic dict.
        data = response.json()
        print("news API returned", data)
        # The News API uses a 'status' field in the body to indicate API-level errors
        if data.get("status") == "error":
            # Explicitly construct the TypedDict from the generic dict data
            error_data = NewsApiErrorResponse(
                status=data["status"], code=data["code"], message=data["message"]
            )
            # Re-raise the API-specific error, but return the typed structure
            raise Exception(
                f"News API Error [{error_data['code']}]: {error_data['message']}"
            )

        # Cast the data to the success type after runtime check
        return cast(NewsApiSuccessResponse, data)

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error occurred: {e}", file=sys.stderr)
        return NewsApiErrorResponse(status="error", code="httpError", message=str(e))
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        # Return a general error response for type safety
        return NewsApiErrorResponse(status="error", code="fetchError", message=str(e))


# --- 4. Example Usage (for demonstration purposes) ---

if __name__ == "__main__":
    search_params: EverythingParams = {
        "q": "Python AND Data Science",
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 5,
        # Note: Use 'from_date' instead of 'from' in the Python parameters
        # 'from_date': '2023-11-01',
    }

    print(f"Fetching news for: {search_params['q']}")

    result = fetch_everything(search_params)

    if result["status"] == "ok":
        # Use cast() to narrow the type for Pylance after the runtime check
        success_result = cast(NewsApiSuccessResponse, result)
        print(f"\nSuccessfully retrieved {success_result['totalResults']} results.")
        for index, article in enumerate(success_result["articles"]):
            print(f"\nArticle {index + 1}:")
            print(f"  Title: {article['title']}")
            print(f"  Source: {article['source']['name']}")
            print(f"  Published: {article['publishedAt']}")
            print(f"  URL: {article['url']}")
    else:
        # Use cast() to narrow the type for Pylance after the runtime check
        error_result = cast(NewsApiErrorResponse, result)
        print("\nAPI call failed.", file=sys.stderr)
        print(f"Error Code: {error_result['code']}", file=sys.stderr)
        print(f"Message: {error_result['message']}", file=sys.stderr)
        # Use cast() to narrow the type for Pylance after the runtime check
        error_result = cast(NewsApiErrorResponse, result)
        print("\nAPI call failed.", file=sys.stderr)
        print(f"Error Code: {error_result['code']}", file=sys.stderr)
        print(f"Message: {error_result['message']}", file=sys.stderr)
