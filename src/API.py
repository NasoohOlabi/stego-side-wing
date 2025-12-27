import atexit
import datetime
import json
import os
import sqlite3
from csv import Error
from functools import wraps
from pathlib import Path
from typing import cast

import dotenv
import ollama
import requests
from flask import Flask, jsonify, make_response, request
from flask_caching import Cache
from icecream import ic
from pydantic import BaseModel, Field

from ai_analyze import process_file
from event_loop_manager import run_async, start_event_loop, stop_event_loop
from headless_browser_analyzer import (
    WebAnalyzer,
    deterministic_hash_sha256,
    normalize_url,
)
from scraper import extract_structured_data
from util.newsApi import (
    EverythingParams,
    NewsApiErrorResponse,
    NewsApiSuccessResponse,
    fetch_everything,
)

dotenv.load_dotenv()

# Define the directory where the processed post JSON files are stored
POSTS_DIRECTORY = "datasets/news_cleaned"


# Initialize the Flask application
app = Flask(__name__)

# Start the persistent event loop at module level
# This ensures it's available before any requests are handled
start_event_loop()

# Suppress LiteLLM debug info to reduce verbose logging
try:
    import litellm

    litellm.suppress_debug_info = True
except ImportError:
    pass  # litellm may not be directly imported, but used by crawl4ai

idx = 0


def is_file_in_folder(folder_path: str, file_name: str):
    """
    Checks if a file exists within a specified folder.

    Args:
      folder_path (str): The path to the folder.
      file_name (str): The name of the file to check.

    Returns:
      bool: True if the file exists in the folder, False otherwise.
    """
    file_full_path = os.path.join(folder_path, file_name)
    return os.path.exists(file_full_path)


steps = {
    "filter-url-unresolved": {
        "source_dir": POSTS_DIRECTORY,
        "dest_dir": "./datasets/news_url_fetched",
    },
    "filter-researched": {
        "source_dir": "./datasets/news_url_fetched",
        "dest_dir": "./datasets/news_researched",
    },
    "angles-step": {
        "source_dir": "./datasets/news_researched",
        "dest_dir": "./datasets/news_angles",
    },
    "final-step": {
        "source_dir": "./datasets/news_angles",
        "dest_dir": "./output-results",
    },
}


@app.route("/posts_list", methods=["GET"])
def posts_list():
    """
    API endpoint to fetch and return a random structured post.
    """
    count = request.args.get("count", type=int)
    step = request.args.get("step", type=str)
    offset = request.args.get("offset", type=int) or 0
    assert count
    assert step in steps
    src_dir = steps[step]["source_dir"]
    dest_dir = steps[step]["dest_dir"]
    try:
        all_files = os.listdir(src_dir)
    except FileNotFoundError:
        # If the directory doesn't exist, the data processing script hasn't run.
        raise FileNotFoundError(
            f"Post directory not found: {src_dir}. Please run data_nesting_script.py first."
        )

    # Filter for files that end with .json
    json_files = [
        f
        for f in all_files
        if f.endswith(".json") and (not is_file_in_folder(dest_dir, f))
    ]

    if not json_files:
        raise ValueError(
            f"No JSON post files found in {POSTS_DIRECTORY}. Check your data processing output."
        )
    return jsonify({"fileNames": json_files[offset : offset + count]}), 200


@app.route("/search", methods=["GET"])
def search():
    """
    deprecated
    """
    q = request.args.get("q", type=str)
    assert q
    search_params: EverythingParams = {
        "q": q,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 5,
        # Note: Use 'from_date' instead of 'from' in the Python parameters
        # 'from_date': '2023-11-01',
    }

    print(f"Fetching news for: {search_params['q']}")
    try:
        result = fetch_everything(search_params)

        if result["status"] == "ok":
            # Use cast() to narrow the type for Pylance after the runtime check
            success_result = cast(NewsApiSuccessResponse, result)
            ic(success_result)
            return jsonify(
                {
                    "results": [
                        {
                            "title": x["title"],
                            "link": x["url"],
                            "snippet": x["description"],
                        }
                        for x in success_result["articles"]
                    ]
                }
            ), 200
        else:
            # Use cast() to narrow the type for Pylance after the runtime check
            error_result = cast(NewsApiErrorResponse, result)
            return jsonify(error_result), 500
    except Error as e:
        return jsonify(e), 500


@app.route("/get_post", methods=["GET"])
def get_post():
    """
    API endpoint to fetch and return a random structured post.
    """
    post = request.args.get("post", type=str)
    step = request.args.get("step", type=str)
    assert post
    assert step in steps
    src_dir = steps[step]["source_dir"]

    with open(
        os.path.join(src_dir, post),
        "r",
        encoding="utf-8",
    ) as f:
        post = json.load(f)
        return jsonify(post), 200


@app.route("/fetch_url_content", methods=["POST"])
async def fetch_url_content():
    url = request.args.get("url", type=str)
    if url is not None:
        url = url.strip()
    if url is None or url == "":
        return jsonify(
            {
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
        ), 200

    # Assuming WebAnalyzer has an async method called process_url
    wa = WebAnalyzer()
    result = wa.process_url(url)

    return jsonify({"message": "Processed", "result": result}), 200


"""
# --- Define Your Schema ---
class ArticleData(BaseModel):
    title: str = Field(..., description="The main headline of the article")
    summary: str = Field(..., description="A 2-sentence summary of the content")
    key_points: list[str] = Field(..., description="List of 3-5 key takeaways")
    author: str = Field(default="Unknown", description="Name of the author if available")

async def main():
    # Target URL (Example: A tech news site)
    target_url = "https://techcrunch.com/" 
    
    # Run the Modular Scraper
    result = await extract_structured_data(
        url=target_url,
        schema=ArticleData,
        # Ensure this matches the ID in LM Studio top bar exactly:
        model_name="mistral-nemo-instruct-2407-abliterated", 
        instruction="Analyze the main article on this page. Ignore nav links and ads."
    )

    # Display Clean Output
    if result:
        print("\n" + "="*40)
        print(" FINAL EXTRACTED DATA ")
        print("="*40)
        print(json.dumps(result, indent=2))
    else:
        print("\n[!] No data extracted.")
"""


class ArticleData(BaseModel):
    title: str = Field(..., description="The main headline of the article")
    summary: str = Field(..., description="A 2-sentence summary of the content")
    key_points: list[str] = Field(..., description="List of 3-5 key takeaways")
    author: str = Field(
        default="Unknown", description="Name of the author if available"
    )


@app.route("/fetch_url_content_crawl4ai", methods=["POST"])
def fetch_url_content_crawl4ai():
    url = request.args.get("url", type=str)
    if url is not None:
        url = url.strip()
    if url is None or url == "":
        return jsonify(
            {
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
        ), 200

    # Normalize URL for better cache hits
    normalized_url = normalize_url(url)
    cache_key = deterministic_hash_sha256(normalized_url)
    filename = f"./datasets/url_cache/{cache_key}.json"

    # Ensure cache directory exists
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    # Check cache first
    try:
        if os.path.exists(filename):
            print(f"📂 Cache HIT for {url}")
            with open(filename, "r", encoding="utf-8") as f:
                cached_response = json.load(f)
                return jsonify(cached_response), 200
    except Exception as e:
        print(f"⚠️ Error reading cache for {url}: {e}")

    print(f"📂 Cache MISS for {url}")

    # Use run_async to ensure all async operations run in the persistent event loop
    # This prevents LiteLLM's LoggingWorker Queue from being bound to a different event loop
    result = run_async(
        extract_structured_data(
            url=url,
            schema=ArticleData,
            # Ensure this matches the ID in LM Studio top bar exactly:
            model_name="mistral-nemo-instruct-2407-abliterated",
            instruction="Analyze the main article on this page. Ignore nav links and ads. extract all the main points, tangents and unique ideas from the article.",
        )
    )

    # Prepare API response
    api_response = {"message": "Processed", "result": result}

    # Save to cache
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(api_response, f, indent=2, ensure_ascii=False)
        print(f"💾 Cached response for {url}")
    except Exception as e:
        print(f"⚠️ Error saving cache for {url}: {e}")

    return jsonify(api_response), 200


@app.route("/process_file", methods=["POST"])
def process_file_endpoint():
    """
    API endpoint to process a file using the process_file function from ai_analyze.
    Expects JSON payload with 'name' field (filename without extension).
    Constructs path as {POSTS_DIRECTORY}/{name}.json
    """

    try:
        # Get the filename from the request
        data = request.get_json()
        if not data or "name" not in data:
            return jsonify({"error": "Missing 'name' in request body"}), 400
        if not data or "step" not in data:
            return jsonify({"error": "Missing 'step' in request body"}), 400

        step = data["step"]
        filename = data["name"]
        assert step in steps
        src_dir = steps[step]["source_dir"]
        dest_dir = steps[step]["dest_dir"]

        # Construct the full file path
        file_path = os.path.join(src_dir, f"{filename}.json")
        dest_file_path = os.path.join(dest_dir, f"{filename}.json")

        # Validate that the file exists
        if not os.path.exists(file_path):
            return jsonify({"error": f"File not found: {file_path}"}), 404

        # Run the async process_file function using persistent event loop
        with open(file_path, "r", encoding="utf-8") as f:
            post = json.load(f)
        # Check if post already has analysis_timestamp
        if os.path.exists(dest_file_path):
            print(f"✅ Post already analyzed on {post['analysis_timestamp']}")
            print("⏭️  Skipping analysis to avoid duplicate work")
            with open(dest_file_path, "r", encoding="utf-8") as f:
                return jsonify(json.load(f)), 201

        print(f"\n{'=' * 60}")
        print(f"📁 Processing file: {file_path}")
        print(f"{'=' * 60}")

        result = run_async(process_file(post))

        # Save the updated post data back to the original file
        print(f"💾 Saving results to file: {dest_file_path}")
        with open(dest_file_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        print(f"✅ Results successfully saved to: {dest_file_path}")
        print("🎉 Analysis complete!")

        return jsonify(
            {
                "message": "File processed successfully",
                "file_path": dest_file_path,
                "result": result,
            }
        )

    except Exception as e:
        return jsonify({"error": f"Error processing file: {str(e)}"}), 500


@app.route("/save-json", methods=["POST"])
def save_json():
    """
    Accepts JSON body and saves it to ./output-results/{timestamp}.json
    """
    try:
        # Parse JSON from request body
        data = request.get_json()

        if data is None:
            return jsonify({"error": "Invalid or missing JSON"}), 400

        # Create output directory if it doesn't exist
        output_dir = Path("./output-results")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate ISO timestamp and make it filesystem-safe
        timestamp = datetime.datetime.now().isoformat()
        # Remove colons for compatibility
        safe_timestamp = timestamp.replace(":", "-")

        # Construct file path
        filepath = output_dir / f"{safe_timestamp}.json"

        # Write JSON to file with pretty formatting
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)

        return jsonify(
            {
                "success": True,
                "message": "JSON saved successfully",
                "filename": filepath.name,
                "path": str(filepath),
            }
        ), 200

    except Exception as e:
        return jsonify({"error": f"Failed to save JSON: {str(e)}"}), 500


@app.route("/", methods=["GET"])
def index():
    """
    Simple welcome message for the API root.
    """
    return "Welcome to the Reddit Post API. Available endpoints: /random_post (GET), /process_file (POST), /generate_keywords (POST)"


DB_FILE = "kv_store.db"
OLD_DB_FILE = "kv_store.json"


def migrate_json_to_sqlite():
    """Migrate data from the old JSON file to SQLite database."""
    if not os.path.exists(OLD_DB_FILE):
        return  # No old file to migrate

    # Initialize database first
    init_db()

    # Check if SQLite database already has data
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM kv")
    existing_count = cursor.fetchone()[0]
    conn.close()

    if existing_count > 0:
        print(
            f"SQLite database already contains {existing_count} entries. Skipping migration."
        )
        return

    print(f"Migrating data from {OLD_DB_FILE} to {DB_FILE}...")

    try:
        # Load data from old JSON file
        with open(OLD_DB_FILE, "r", encoding="utf-8") as f:
            old_data = json.load(f)

        if not old_data:
            print("No data found in old JSON file.")
            return

        # Insert all key-value pairs into SQLite
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()

        migrated_count = 0
        for key, value in old_data.items():
            # Serialize value to JSON string for storage
            value_json = json.dumps(value)
            cursor.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                (key, value_json),
            )
            migrated_count += 1

        conn.commit()
        conn.close()

        print(f"Successfully migrated {migrated_count} key-value pairs to SQLite.")

        # Backup the old file by renaming it
        backup_file = f"{OLD_DB_FILE}.backup"
        if os.path.exists(backup_file):
            os.remove(backup_file)
        os.rename(OLD_DB_FILE, backup_file)
        print(f"Old JSON file backed up to {backup_file}")

    except Exception as e:
        print(f"Error during migration: {str(e)}")
        raise


def init_db():
    """Initialize the SQLite database and create the kv table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


@app.route("/set", methods=["POST"])
def set_value():
    """
    POST endpoint to set a key-value pair.
    Expects JSON body: { "key": "your_key", "value": "your_value" }
    """
    data = request.get_json(force=True, silent=True)

    if not data or "key" not in data or "value" not in data:
        return jsonify({"error": 'Missing "key" or "value" in request body'}), 400

    key = str(data["key"])
    value = data["value"]

    # Serialize value to JSON string for storage
    value_json = json.dumps(value)

    # Insert or replace the key-value pair in SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value_json)
    )
    conn.commit()
    conn.close()

    return jsonify(
        {"status": "success", "message": f'Key "{key}" saved.', "data": {key: value}}
    ), 201


@app.route("/get/<k>", methods=["GET"])
def get_value(k):
    """
    GET endpoint to retrieve a value by key.
    Usage: GET /get/myKey
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM kv WHERE key = ?", (k,))
    row = cursor.fetchone()
    conn.close()

    if row:
        # Deserialize the JSON value
        value = json.loads(row[0])
        return jsonify({"k": k, "v": value}), 200
    else:
        return jsonify({"error": f'Key "{k}" not found'}), 404


# CONFIGURATION FOR PERSISTENCE
cache = Cache(
    config={
        "CACHE_TYPE": "FileSystemCache",  # Store on disk, not RAM
        "CACHE_DIR": "cache-directory",  # Folder name (will be created auto)
        "CACHE_DEFAULT_TIMEOUT": 9999999,  # ~115 days (effectively permanent)
        "CACHE_THRESHOLD": 10000,  # Max number of items to store
    }
)
cache.init_app(app)

OLLAMA_API_KEY = dotenv.get_key(".env", "OLLAMA_API_KEY")
assert OLLAMA_API_KEY
client = ollama.Client(
    host="https://ollama.com", headers={"Authorization": "Bearer " + OLLAMA_API_KEY}
)


@app.route("/ollama_search", methods=["GET"])
# Use the default timeout set above, or override here
@cache.cached(query_string=True)
def ollama_search():
    q = request.args.get("q", type=str)
    if not q:
        return jsonify({"error": "No query"}), 400

    print(f"Fetching from Ollama: {q}")  # Debug: Will only print on Cache Miss

    try:
        response = client.web_search(q)
        return jsonify(
            [
                {"title": x.title, "url": x.url, "content": x.content}
                for x in response.results
            ]
        ), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/bing_search", methods=["GET"])
@cache.cached(timeout=300, query_string=True)
def bing_search():
    """
    Proxy endpoint that wraps ScrapingDog Bing search API.
    Expects query parameters:
      - query: the search query string (required)
      - first: starting index (optional, default 1)
      - count: number of results to return (optional, default 10)
    Requires ScrapingDog API key via SCRAPINGDOG_API_KEY env var (or SCRAPINGDOG_API_KEY in .env).
    """
    query = request.args.get("query", type=str)
    first = request.args.get("first", default=1, type=int)
    count = request.args.get("count", default=10, type=int)

    if not query:
        return jsonify({"error": "Missing 'query' parameter"}), 400

    # Retrieve API key from environment or dotenv
    api_key = os.environ.get("SCRAPINGDOG_API_KEY")
    if not api_key:
        api_key = dotenv.get_key(".env", "SCRAPINGDOG_API_KEY")
    if not api_key:
        return jsonify({"error": "ScrapingDog API key not configured"}), 500

    params = {"query": query, "first": first, "count": count, "api_key": api_key}

    try:
        resp = requests.get(
            "https://api.scrapingdog.com/bing/search", params=params, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        return jsonify(
            {
                "results": [
                    {"title": x["title"], "link": x["link"], "snippet": x["snippet"]}
                    for x in data["bing_data"]
                ]
            }
        ), 200
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 500


def add_cache_header(func):
    """Decorator to add X-Cache-Status header to responses.
    Must wrap the @cache.cached decorator to intercept responses.
    Checks cache before function execution to determine cache hit status.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Generate cache key the same way Flask-Caching does when query_string=True
        # Flask-Caching format: view/{endpoint}.{function_name}
        # With query_string=True, it appends the query string hash
        import hashlib
        from urllib.parse import parse_qsl, urlencode

        # Get the endpoint name (Flask-Caching uses request.endpoint)
        endpoint = request.endpoint or "google_search"
        func_name = func.__name__
        base_key = f"view/{endpoint}.{func_name}"

        # Build query string the same way Flask-Caching does
        # Flask-Caching sorts query params and hashes them
        query_params = dict(parse_qsl(request.query_string.decode("utf-8")))
        if query_params:
            # Sort and encode query params (Flask-Caching does this)
            sorted_params = sorted(query_params.items())
            query_str = urlencode(sorted_params)
            query_hash = hashlib.md5(query_str.encode("utf-8")).hexdigest()
            cache_key = f"{base_key}?{query_hash}"
        else:
            cache_key = base_key

        # Check if cache exists BEFORE calling the function
        # Flask-Caching stores the full response tuple (data, status_code, headers)
        cached_value = cache.get(cache_key)
        is_cache_hit = cached_value is not None

        # Call the original function (which may return cached response from @cache.cached)
        response = func(*args, **kwargs)

        # Convert tuple response to Flask Response object while preserving status code
        if isinstance(response, tuple):
            if len(response) == 2:
                # (data, status_code)
                response_obj = make_response(response[0], response[1])
            elif len(response) == 3:
                # (data, status_code, headers)
                response_obj = make_response(response[0], response[1], response[2])
            else:
                response_obj = make_response(response[0])
        else:
            response_obj = make_response(response)

        # Add cache status header
        # Status code remains 200 for cache hits (Flask-Caching preserves original status)
        response_obj.headers["X-Cache-Status"] = "HIT" if is_cache_hit else "MISS"

        return response_obj

    return wrapper


@app.route("/google_search", methods=["GET"])
def google_search():
    """
    Proxy endpoint that wraps Google Custom Search API.
    Expects query parameters:
      - query: the search query string (required)
      - first: starting index (optional, default 1)
      - count: number of results to return (optional, default 10)
    Requires Google API key and Custom Search Engine ID via GOOGLE_API_KEY_1-4 and GOOGLE_CSE_ID env vars (or in .env).
    Tries each API key sequentially until one succeeds.
    Only caches successful responses (200 status), not errors.
    """
    import hashlib
    from urllib.parse import parse_qsl, urlencode

    # Generate cache key the same way Flask-Caching does when query_string=True
    endpoint = request.endpoint or "google_search"
    func_name = "google_search"
    base_key = f"view/{endpoint}.{func_name}"

    # Build query string the same way Flask-Caching does
    query_params = dict(parse_qsl(request.query_string.decode("utf-8")))
    if query_params:
        sorted_params = sorted(query_params.items())
        query_str = urlencode(sorted_params)
        query_hash = hashlib.md5(query_str.encode("utf-8")).hexdigest()
        cache_key = f"{base_key}?{query_hash}"
    else:
        cache_key = base_key

    # Check cache first (only for successful responses)
    cached_value = cache.get(cache_key)
    if cached_value is not None:
        # Return cached successful response
        if isinstance(cached_value, tuple):
            if len(cached_value) == 2:
                response_obj = make_response(cached_value[0], cached_value[1])
            elif len(cached_value) == 3:
                response_obj = make_response(
                    cached_value[0], cached_value[1], cached_value[2]
                )
            else:
                response_obj = make_response(cached_value[0])
        else:
            response_obj = make_response(cached_value)
        response_obj.headers["X-Cache-Status"] = "HIT"
        return response_obj

    query = request.args.get("query", type=str)
    first = request.args.get("first", default=1, type=int)
    count = request.args.get("count", default=10, type=int)

    if not query:
        response = jsonify({"error": "Missing 'query' parameter"}), 400
        response_obj = make_response(response[0], response[1])
        response_obj.headers["X-Cache-Status"] = "MISS"
        return response_obj

    # Retrieve Custom Search Engine ID from environment or dotenv
    cse_id = os.environ.get("GOOGLE_CSE_ID")
    if not cse_id:
        cse_id = dotenv.get_key(".env", "GOOGLE_CSE_ID")
    if not cse_id:
        response = (
            jsonify({"error": "Google Custom Search Engine ID not configured"}),
            500,
        )
        response_obj = make_response(response[0], response[1])
        response_obj.headers["X-Cache-Status"] = "MISS"
        return response_obj

    # Retrieve API keys from environment or dotenv (try GOOGLE_API_KEY_1 through GOOGLE_API_KEY_4)
    api_keys = []
    for i in range(1, 6):
        key_name = f"GOOGLE_API_KEY_{i}"
        api_key = os.environ.get(key_name)
        if not api_key:
            api_key = dotenv.get_key(".env", key_name)
        if api_key:
            api_keys.append(api_key)

    if not api_keys:
        response = jsonify({"error": "No Google API keys configured"}), 500
        response_obj = make_response(response[0], response[1])
        response_obj.headers["X-Cache-Status"] = "MISS"
        return response_obj

    # Try each API key sequentially until one succeeds
    errors = []
    for api_key in api_keys:
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": count,
            "start": first,
        }
        data = None
        try:
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1", params=params, timeout=20
            )
            data = resp.json()
            resp.raise_for_status()

            # Check for error in JSON response (Google API sometimes returns errors in JSON with 200 status)
            if "error" in data:
                error_info = data.get("error", {})
                error_message = error_info.get("message", "Unknown error")
                errors.append(
                    {
                        "key_index": api_keys.index(api_key) + 1,
                        "error": f"API error: {error_message}",
                        "data": data,
                    }
                )
                # log the data
                print("-" * 100)
                print("API error: {error_message}")
                print("-" * 100)
                print(data)
                print("-" * 100)
                continue

            # Check if "items" key exists (may be missing if no results)
            if "items" not in data:
                print("-" * 100)
                print("No items found")
                print("-" * 100)
                print(data)
                print("-" * 100)
                # No results found - return empty results instead of error
                response = jsonify({"results": []}), 200
                # Cache successful response (200 status)
                cache.set(cache_key, response, timeout=300)
                response_obj = make_response(response[0], response[1])
                response_obj.headers["X-Cache-Status"] = "MISS"
                return response_obj

            # Success - return the results
            response = (
                jsonify(
                    {
                        "results": [
                            {
                                "title": x.get("title", ""),
                                "link": x.get("link", ""),
                                "snippet": x.get("snippet", ""),
                            }
                            for x in data["items"]
                        ]
                    }
                ),
                200,
            )
            # Cache successful response (200 status)
            cache.set(cache_key, response, timeout=300)
            response_obj = make_response(response[0], response[1])
            response_obj.headers["X-Cache-Status"] = "MISS"
            return response_obj
        except requests.RequestException as e:
            # Store error and try next key
            errors.append(
                {
                    "key_index": api_keys.index(api_key) + 1,
                    "error": str(e),
                    "data": data,
                }
            )
            continue

    # All API keys failed - DO NOT cache errors
    response = (
        jsonify(
            {
                "error": f"All API {len(api_keys)} keys failed to fetch results",
                "errors": errors,
            }
        ),
        500,
    )
    response_obj = make_response(response[0], response[1])
    response_obj.headers["X-Cache-Status"] = "MISS"
    return response_obj


# Cleanup on app shutdown
atexit.register(stop_event_loop)


if __name__ == "__main__":
    # To run this script, you must first install Flask: pip install Flask
    # Start the persistent event loop
    start_event_loop()

    # Migrate data from old JSON file to SQLite if it exists
    migrate_json_to_sqlite()
    # Initialize database (creates table if it doesn't exist)
    init_db()
    print(f"Serving posts from directory: {os.path.abspath(POSTS_DIRECTORY)}")
    print(f"Database file: {os.path.abspath(DB_FILE)}")
    print("Starting server...")
    try:
        app.run(debug=False, port=5000)
    finally:
        stop_event_loop()
