import asyncio
import json
import os
import sys
from datetime import datetime
from typing import List, cast

import httpx
from flask import cli
from icecream import ic  # Import icecream for colorful logging
from openai import OpenAI

from headless_browser_analyzer import WebAnalyzer
from util import DuckDuckApi
from util.newsApi import (Article, EverythingParams, NewsApiErrorResponse,
                          NewsApiSuccessResponse, fetch_everything)

# Configure icecream to include timestamps
ic.configureOutput(prefix="[{time}] | ")

_original_httpx_client_init = httpx.Client.__init__


def _httpx_client_init_with_proxies(self, *args, proxies=None, **kwargs):
    if proxies is not None and "proxy" not in kwargs:
        kwargs["proxy"] = proxies
    return _original_httpx_client_init(self, *args, **kwargs)


httpx.Client.__init__ = _httpx_client_init_with_proxies

MODEL_URL = "http://192.168.100.136:1234/v1"
MODEL_NAME = "mistral-nemo-instruct-2407-abliterated"
# MODEL_NAME = "openai/gpt-oss-20b"

# Point to the local server URL provided by LM Studio
client = OpenAI(
    base_url=MODEL_URL,  # Adjust this if your port is different
    # The API key can be anything; LM Studio doesn't use it.
    api_key="lm-studio",
)

# return the post data
ic(client)


async def process_file(post_data):
    """Process a single JSON file for analysis"""

    # Store comments separately before removing them
    comments = post_data.get("comments", {})
    post_data.pop("comments", None)
    post = json.dumps(post_data)

    prompt = f"""
    You are an expert at extracting specific, detailed topics from articles and rephrasing them into concise, actionable search queries. Your task is to identify key issues, decisions, and technical details discussed by the author, and present them as a list of bullet points.

    Here is the article

    ---

    {post}
    """

    # Define the messages for the conversation
    messages = [
        {
            "role": "system",
            "content": """You are an expert at extracting specific, detailed list of topics from a post and rephrasing them into concise, actionable search queries. The output should only be a JSON list of topics formatted as search-friendly queries. Do not include any personal details, such as the author's name or their emotional state. Focus only on the technical and situational topics that someone might want to research.

    output format OR I'LL KILL MYSELF: ["topic1", "topic2", "topic3"]
""",
        },
        {"role": "user", "content": prompt},
    ]

    global MODEL_NAME
    ic(MODEL_NAME)
    print("🤖 Sending request to AI model for topic extraction")
    # Create the chat completion request
    completion = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,  # type: ignore
        temperature=0.7,
        stream=False,  # Set to True to receive the response in chunks
    )

    # Process the response
    print("📝 AI Response received:")

    full_response: str = completion.choices[0].message.content

    ic(full_response)
    start = full_response.find("[")
    end = len(full_response) - full_response[::-1].find("]")
    full_response = full_response[start:end]
    ic(full_response)

    try:
        topics = json.loads(full_response)
        print(f"📋 Successfully parsed {len(topics)} topics from AI response")
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON response: {e}")
        print(f"Raw response: {full_response}")
        raise

    print(f"\n📋 Extracted {len(topics)} topics:")
    for i, topic in enumerate(topics, 1):
        print(f"{i}. {topic}")

    print("\n" + "=" * 50)
    print("🔍 SEARCHING FOR EACH TOPIC")
    print("=" * 50)

    # Initialize web analyzer - reuse for all URLs in this batch
    web_analyzer = WebAnalyzer()
    web_analyzer._auto_close = False  # Don't auto-close, we'll close manually at the end
    print("🌐 Web analyzer initialized (will reuse browser for all URLs)")

    # get the results of each topic
    all_results = {}

    for i, topic in enumerate(topics, 1):
        print(f"\n🔍 Searching topic {i}/{len(topics)}: '{topic}'")
        try:
            # Use the async search function
            search_params: EverythingParams = {
                "q": topic,
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": 5,
                # Note: Use 'from_date' instead of 'from' in the Python parameters
                # 'from_date': '2023-11-01',
            }

            print(f"Fetching news for: {search_params['q']}")

            result = fetch_everything(search_params)

            search_results: List[Article] = []
            if result["status"] == "ok":
                # Use cast() to narrow the type for Pylance after the runtime check
                success_result = cast(NewsApiSuccessResponse, result)
                print(
                    f"\nSuccessfully retrieved {success_result['totalResults']} results."
                )
                for index, article in enumerate(success_result["articles"]):
                    search_results.append(article)
            else:
                # Use cast() to narrow the type for Pylance after the runtime check
                error_result = cast(NewsApiErrorResponse, result)
                print("\nAPI call failed.", file=sys.stderr)
                print(f"Error Code: {error_result['code']}", file=sys.stderr)
                print(f"Message: {error_result['message']}", file=sys.stderr)

            all_results[topic] = search_results

            print(f"✅ Found {len(search_results)} results for '{topic}'")

            # Display first result as preview
            if search_results:
                first_result = search_results[0]
                print(f"   📄 Sample: {first_result['title'][:60]}...")

            # Fetch and analyze content from URLs
            print(f"   🌐 Fetching content from URLs...")
            # Process top 3 results
            augmented_results = []
            for j, result in enumerate(search_results[:3], 1):
                print(f"   📖 Processing result {j}: {result['title'][:50]}...")
                print(json.dumps(result, indent=2))
                print("_" * 20 + "\n")
                try:
                    # Extract text content from the URL (reusing browser, no auto-close)
                    print(f"      🔗 Attempting to fetch: {result['url']}")
                    content = web_analyzer.process_url(result["url"], auto_close=False)
                    augmented_results.append(
                        {
                            "fetched_content": content,
                            "content_fetched": True,
                            "url": result,
                        }
                    )

                except Exception as e:
                    print(f"      ❌ Error fetching content: {str(e)}")
                    augmented_results.append(
                        {"content_fetched": False, "url": result})

                    # If it's a localhost redirect issue, try a different approach
                    if "localhost" in str(e) or "127.0.0.1" in str(e):
                        print(
                            f"      🔄 Localhost redirect detected, skipping this URL"
                        )
                        continue

        except Exception as e:
            print(f"❌ Error searching for '{topic}': {str(e)}")
            all_results[topic] = []

    # Close the browser after processing all URLs
    try:
        web_analyzer.close()
        print("🔒 Browser closed after batch processing")
    except Exception as e:
        print(f"⚠️ Warning: Error closing browser: {e}")

    print("\n" + "=" * 50)
    print("📊 SEARCH RESULTS SUMMARY")
    print("=" * 50)

    # Display summary of all results
    for topic, results in all_results.items():
        print(f"\n📌 {topic}")
        print(f"   Found {len(results)} results")

        if results:
            # Show top 3 results
            for j, result in enumerate(results[:3], 1):
                print(f"   {j}. {result['title']}")
                print(f"      🔗 {result['link']}")
                if result["snippet"]:
                    snippet = (
                        result["snippet"][:100] + "..."
                        if len(result["snippet"]) > 100
                        else result["snippet"]
                    )
                    print(f"      📝 {snippet}")

                # Show fetched content analysis if available
                if result.get("content_fetched"):
                    print(
                        f"      🤖 AI Analysis: {result['content_analysis'][:200]}..."
                    )
                elif result.get("fetch_error"):
                    print(f"      ❌ Fetch Error: {result['fetch_error']}")
                print()

    # Add the new fields to the original post data
    post_data["extracted_topics"] = topics
    post_data["search_results"] = all_results
    post_data["analysis_timestamp"] = datetime.now().isoformat()

    # Add comments back to the data
    post_data["comments"] = comments

    return post_data


async def main():
    """Main function to process all JSON files in datasets/news and datasets/javahelp directories"""
    # Directories to process
    directories = ["./datasets/news", "./datasets/javahelp"]

    # Collect all JSON files from both directories
    json_files = []

    ic("🔍 Starting file discovery process")
    for directory in directories:
        if os.path.exists(directory):
            ic(f"🔍 Scanning directory: {directory}")
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file.endswith(".json"):
                        file_path = os.path.join(root, file)
                        json_files.append(file_path)
                        ic(f"   📄 Found: {file_path}")
        else:
            ic(f"⚠️  Directory not found: {directory}")

    ic(f"\n📊 Total files to process: {len(json_files)}")

    if not json_files:
        ic("❌ No JSON files found in the specified directories")
        return

    # Process each file
    for i, file_path in enumerate(json_files, 1):
        ic(f"\n🔄 Processing file {i}/{len(json_files)}")
        await process_file(file_path)


if __name__ == "__main__":
    ic("🚀 Starting the analysis process")
    asyncio.run(main())
    ic("✅ Analysis process completed")
