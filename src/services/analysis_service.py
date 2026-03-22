"""Analysis service for processing posts and URLs."""
import json
import logging
import os
from typing import Dict, Optional

from infrastructure.config import STEPS
from infrastructure.event_loop import run_async
from pydantic import BaseModel, Field

# Import pipeline functions
from pipelines.ai_analyze import process_file
from pipelines.scraper import extract_structured_data

logger = logging.getLogger(__name__)


class ArticleData(BaseModel):
    """Schema for article extraction."""
    title: str = Field(..., description="The main headline of the article")
    summary: str = Field(..., description="A 2-sentence summary of the content")
    key_points: list[str] = Field(..., description="List of 3-5 key takeaways")
    author: str = Field(
        default="Unknown", description="Name of the author if available"
    )


def process_post_file(filename: str, step: str) -> Dict:
    """
    Process a post file using AI analysis.
    
    Args:
        filename: Filename without extension
        step: Step name (must be in STEPS)
        
    Returns:
        Dict with processing result
        
    Raises:
        ValueError: If step is invalid
        FileNotFoundError: If file doesn't exist
    """
    if step not in STEPS:
        raise ValueError(f"Invalid step: {step}")
    
    src_dir = STEPS[step]["source_dir"]
    dest_dir = STEPS[step]["dest_dir"]
    
    file_path = os.path.join(src_dir, f"{filename}.json")
    dest_file_path = os.path.join(dest_dir, f"{filename}.json")

    # Validate that the file exists
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Run the async process_file function using persistent event loop
    with open(file_path, "r", encoding="utf-8") as f:
        post = json.load(f)
    
    # Check if post already has analysis_timestamp
    if os.path.exists(dest_file_path):
        logger.info(
            "process_post_file_skip",
            extra={
                "event": "analysis",
                "action": "process_file",
                "step": step,
                "file_name": filename,
                "reason": "already_analyzed",
            },
        )
        with open(dest_file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    logger.info(
        "process_post_file_start",
        extra={
            "event": "analysis",
            "action": "process_file",
            "step": step,
            "file_name": filename,
            "source_path": file_path,
        },
    )

    result = run_async(process_file(post))

    # Save the updated post data back to the original file
    with open(dest_file_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    logger.info(
        "process_post_file_done",
        extra={
            "event": "analysis",
            "action": "process_file",
            "step": step,
            "file_name": filename,
            "dest_path": dest_file_path,
        },
    )

    return {
        "message": "File processed successfully",
        "file_path": dest_file_path,
        "result": result,
    }


def fetch_url_content_crawl4ai(url: str) -> Dict:
    """
    Fetch URL content using crawl4ai with caching.
    
    Args:
        url: URL to fetch
        
    Returns:
        Dict with fetched content or cached result
    """
    from infrastructure.cache import (
        deterministic_hash_sha256,
        read_json_cache,
        write_json_cache,
    )
    from pipelines.headless_browser_analyzer import normalize_url

    if not url or url.strip() == "":
        return {
            "message": "Processed",
            "result": {
                "url": url,
                "success": False,
                "content_type": None,
                "text": None,
                "data": None,
                "analysis": None,
                "error": None,
            },
        }

    # Normalize URL for better cache hits
    normalized_url = normalize_url(url)
    cache_key = deterministic_hash_sha256(normalized_url)
    filename = f"./datasets/url_cache/{cache_key}.json"

    # Check cache first
    cached_response = read_json_cache(filename)
    if cached_response:
        logger.info(
            "url_fetch_cache_hit",
            extra={"event": "analysis", "action": "fetch_url_crawl4ai", "url": url},
        )
        return cached_response

    logger.info(
        "url_fetch_cache_miss",
        extra={"event": "analysis", "action": "fetch_url_crawl4ai", "url": url},
    )

    # Use run_async to ensure all async operations run in the persistent event loop
    result = run_async(
        extract_structured_data(
            url=url,
            schema=ArticleData,
            model_name="mistral-nemo-instruct-2407-abliterated",
            instruction="Analyze the main article on this page. Ignore nav links and ads. extract all the main points, tangents and unique ideas from the article.",
        )
    )

    # Prepare API response
    api_response = {"message": "Processed", "result": result}

    # Save to cache
    write_json_cache(filename, api_response)
    logger.info(
        "url_fetch_cached",
        extra={"event": "analysis", "action": "fetch_url_crawl4ai", "url": url},
    )

    return api_response


def fetch_url_content(url: str) -> Dict:
    """
    Fetch URL content using WebAnalyzer.
    
    Args:
        url: URL to fetch
        
    Returns:
        Dict with fetched content
    """
    from pipelines.headless_browser_analyzer import WebAnalyzer

    if not url or url.strip() == "":
        return {
            "message": "Processed",
            "result": {
                "url": url,
                "success": False,
                "content_type": None,
                "text": None,
                "data": None,
                "analysis": None,
                "error": None,
            },
        }

    logger.info(
        "fetch_url_webanalyzer",
        extra={"event": "analysis", "action": "fetch_url", "url": url},
    )
    wa = WebAnalyzer()
    result = wa.process_url(url)

    return {"message": "Processed", "result": result}
