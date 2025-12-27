import hashlib
import json
import os
import time
import traceback
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def normalize_url(url: str) -> str:
    """
    Normalize URL by removing fragments and common tracking query parameters.
    This improves cache hit rates by treating URLs with different tracking params as the same.
    """
    try:
        parsed = urlparse(url)
        # Remove fragment
        parsed = parsed._replace(fragment="")
        
        # Remove common tracking parameters
        tracking_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "ref", "source", "fbclid", "gclid", "msclkid", "_ga", "_gid",
            "mc_cid", "mc_eid", "ncid", "ncid", "yclid", "twclid"
        }
        
        if parsed.query:
            query_params = parse_qs(parsed.query, keep_blank_values=False)
            # Filter out tracking params
            filtered_params = {k: v for k, v in query_params.items() 
                             if k.lower() not in tracking_params}
            
            # Rebuild query string
            if filtered_params:
                new_query = urlencode(filtered_params, doseq=True)
                parsed = parsed._replace(query=new_query)
            else:
                parsed = parsed._replace(query="")
        
        return urlunparse(parsed)
    except Exception:
        # If normalization fails, return original URL
        return url


def deterministic_hash_sha256(input_string):
    """
    Hashes a string deterministically using SHA-256.
    The same input string will always produce the same hash.
    """
    # Encode the string to bytes (UTF-8 is a common and robust choice)
    encoded_string = input_string.encode("utf-8")

    # Create a SHA-256 hash object
    hasher = hashlib.sha256()

    # Update the hash object with the encoded string
    hasher.update(encoded_string)

    # Get the hexadecimal representation of the hash
    return hasher.hexdigest()


class WebAnalyzer:
    def __init__(self, max_cache_files: int = 10000):
        """
        Initialize WebAnalyzer.
        
        Args:
            max_cache_files: Maximum number of cache files to keep. When exceeded, oldest files are deleted.
                             Set to None to disable cache rotation.
        """
        self.driver = None
        self._auto_close = True  # Default: auto-close after each process_url (backward compat)
        self.max_cache_files = max_cache_files

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - always close driver."""
        self.close()

    def close(self):
        """Explicitly close the browser driver. Safe to call multiple times."""
        self._close_driver()

    def process_url(self, url: str, task: str = "summarize", timeout: int = 15, auto_close: bool = None) -> dict:
        """
        Main public entry point. Handles the lifecycle and top-level error catching.
        
        Args:
            url: URL to process
            task: Task type (currently unused, kept for backward compatibility)
            timeout: Overall timeout in seconds for processing this URL (default: 15)
            auto_close: Whether to close driver after this call. If None, uses instance default.
        
        Returns:
            dict with url, success, text, error fields
        """
        start_time = time.time()
        
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
                    cached_result = json.load(f)
                    elapsed = time.time() - start_time
                    print(f"⏱️  Cache retrieval took {elapsed:.2f}s")
                    return cached_result
        except Exception as e:
            print(f"⚠️ Error reading cache for {url}: {e}")

        print(f"📂 Cache MISS for {url}")

        result = {
            "url": url,
            "success": False,
            "text": None,
            "error": None,
        }

        # Determine if we should auto-close
        should_close = auto_close if auto_close is not None else self._auto_close

        try:
            # Try fast HTTP path first (for simple pages)
            text = self._try_fast_http_fetch(normalized_url, min_len=100)
            if text:
                print(f"✅ Fast HTTP fetch succeeded: {len(text)} chars")
                result["text"] = text
                result["success"] = True
            else:
                # Fall back to Selenium for JS-heavy pages
                self._initialize_browser_cleaned()
                print(f"🌐 Processing with Selenium: {url}")
                text = self._extract_text_content(normalized_url, timeout=timeout)
                result["text"] = text
                result["success"] = True

            # Save to cache (store minimal fields)
            cache_data = {
                "url": url,
                "success": result["success"],
                "text": result["text"],
            }
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
            print(f"✅ Saved to cache: {filename}")
            
            # Optional: Rotate cache if it's too large
            if self.max_cache_files is not None:
                self._rotate_cache_if_needed()

        except Exception as e:
            result["error"] = str(e)
            print(f"❌ Error processing {url}: {e}")
            traceback.print_exc()
        finally:
            # Only close if auto_close is enabled
            if should_close:
                self._close_driver()
        
        elapsed = time.time() - start_time
        print(f"⏱️  Total processing time: {elapsed:.2f}s")
        return result

    def _initialize_browser_cleaned(self):
        """Initialize the browser instance with a clean, de-duplicated set of options."""
        if self.driver is None:
            chrome_options = Options()

            # 1. System/Container Stability (Essential for Docker/Linux)
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")

            # 2. Performance & Backgrounding (Preventing Throttling/Sleeping)
            # Prevents Chrome from slowing down or killing background processes.
            chrome_options.add_argument("--disable-background-networking")
            chrome_options.add_argument("--disable-background-timer-throttling")
            chrome_options.add_argument("--disable-renderer-backgrounding")
            chrome_options.add_argument("--disable-backgrounding-occluded-windows")
            chrome_options.add_argument("--disable-hang-monitor")
            chrome_options.add_argument("--disable-domain-reliability")
            chrome_options.add_argument("--disable-background-mode")

            # 3. Security & SSL Bypasses
            chrome_options.add_argument("--ignore-ssl-errors")
            chrome_options.add_argument("--ignore-certificate-errors")
            chrome_options.add_argument("--ignore-certificate-errors-spki-list")
            chrome_options.add_argument("--ignore-ssl-errors-spki-list")
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--allow-running-insecure-content")
            chrome_options.add_argument("--disable-client-side-phishing-detection")
            chrome_options.add_argument("--disable-prompt-on-repost")

            # 4. UI/Bloat Reduction
            chrome_options.add_argument("--disable-sync")
            chrome_options.add_argument("--disable-default-apps")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-popup-blocking")
            chrome_options.add_argument("--disable-features=TranslateUI")
            chrome_options.add_argument("--disable-features=VizDisplayCompositor")
            chrome_options.add_argument("--disable-component-extensions-with-background-pages")
            chrome_options.add_argument("--disable-ipc-flooding-protection")
            chrome_options.add_argument("--no-first-run")

            # 5. Headless mode and window size
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1280,720")

            # 6. Anti-Detection & Logging Suppression
            chrome_options.add_argument(
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_argument("--log-level=3")
            chrome_options.add_argument("--silent")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
            chrome_options.add_experimental_option("useAutomationExtension", False)

            # 7. Block non-essential resources to save memory and bandwidth
            prefs = {
                "profile.managed_default_content_settings.images": 2,  # Block images
                "profile.default_content_setting_values.media_stream": 2,  # Block media
                "profile.managed_default_content_settings.stylesheets": 1,  # Allow CSS (needed for layout)
                "profile.default_content_settings.popups": 0,  # Block popups
                "profile.managed_default_content_settings.plugins": 2,  # Block plugins
            }
            chrome_options.add_experimental_option("prefs", prefs)

            # Initialize the driver
            self.driver = webdriver.Chrome(options=chrome_options)

    def _close_driver(self):
        """Internal: Cleanup."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                print(f"⚠️ Warning: Error closing driver: {e}")
            finally:
                self.driver = None

    def _parse_html(self, html_content: str) -> str:
        """Internal: Centralized logic to clean HTML and get text."""
        if not html_content or len(html_content) < 100:
            return ""
        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        return " ".join(soup.get_text().split())

    def _stop_js(self):
        """Internal: Force stop JS execution."""
        if not self.driver:
            return
        try:
            self.driver.execute_script("window.stop();")
            # Clear intervals/timeouts aggressively
            self.driver.execute_script("""
                let id = window.setTimeout(function() {}, 0);
                while (id--) { window.clearTimeout(id); window.clearInterval(id); }
            """)
        except:
            pass

    def _wait_for_stable_content(self, timeout=10):
        """Internal: Smart wait that monitors content length stability."""
        start, last_len, stable_checks = time.time(), 0, 0
        assert self.driver
        while time.time() - start < timeout:
            try:
                if (
                    self.driver.execute_script("return document.readyState")
                    == "complete"
                ):
                    # If ready, check stability quickly
                    curr_len = len(self._parse_html(self.driver.page_source))
                    if curr_len == last_len and curr_len > 0:
                        stable_checks += 1
                        if stable_checks >= 3:  # More aggressive: stable for ~1.5s
                            break
                    else:
                        last_len = curr_len
                        stable_checks = 0
                    break

                curr_len = len(self._parse_html(self.driver.page_source))
                if curr_len > last_len:
                    last_len = curr_len
                    stable_checks = 0
                elif curr_len == last_len and curr_len > 0:
                    stable_checks += 1
                    if stable_checks >= 3:  # More aggressive: stable for ~1.5s
                        break

                time.sleep(0.5)
            except:
                break

    def _try_fast_http_fetch(self, url: str, min_len: int = 100, timeout: int = 5) -> str:
        """
        Try to fetch content using simple HTTP request + BeautifulSoup.
        Returns empty string if it fails or content is too short.
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            
            # Parse with BeautifulSoup
            text = self._parse_html(response.text)
            
            if len(text) >= min_len:
                return text
        except Exception as e:
            # Silently fail - we'll fall back to Selenium
            pass
        
        return ""

    def _extract_text_content(self, url: str, min_len: int = 50, timeout: int = 12) -> str:
        """Internal: Navigates and tries multiple strategies to get text."""
        if not self.driver:
            raise RuntimeError("Driver not initialized")

        overall_start = time.time()

        # Initial Navigation with timeout
        try:
            self.driver.set_page_load_timeout(timeout)
            self.driver.get(url)
            self._wait_for_stable_content(timeout=min(10, timeout - 2))
        except Exception:
            print("⚠️ Navigation timeout, attempting extraction anyway...")
            self._stop_js()

        # Check overall timeout
        if time.time() - overall_start > timeout:
            raise RuntimeError(f"Overall timeout ({timeout}s) exceeded")

        def standard():
            assert self.driver
            return self.driver.page_source

        def stop_js():
            assert self.driver
            self._stop_js()
            time.sleep(1)  # Reduced from 2s
            return self.driver.page_source

        def wait_more():
            assert self.driver
            time.sleep(2)  # Reduced from 5s
            return self.driver.page_source

        def body_tag():
            assert self.driver
            return self.driver.find_element(By.TAG_NAME, "body").text

        # Strategy Execution Loop
        strategies = [
            ("Standard", standard),
            ("StopJS", stop_js),
            ("BodyTag", body_tag),
            ("Selectors", lambda: self._extract_from_selectors()),
            ("WaitMore", wait_more),  # Moved to end as it's slower
        ]

        best_text = ""

        for name, strategy in strategies:
            # Check timeout before each strategy
            if time.time() - overall_start > timeout:
                break
                
            try:
                raw_content = strategy()
                # If strategy returned raw HTML, clean it; if text, clean it lightly
                text = (
                    raw_content
                    if name in ["BodyTag", "Selectors"]
                    else self._parse_html(raw_content)
                )

                if len(text) > len(best_text):
                    best_text = text

                if len(text) >= min_len:
                    print(f"✅ Extracted {len(text)} chars using {name} strategy")
                    return text
            except Exception:
                continue

        if len(best_text) >= min_len:
            return best_text

        raise RuntimeError(
            f"Failed to extract sufficient content. Got {len(best_text)} chars."
        )

    def _extract_from_selectors(self) -> str:
        """Internal: Fallback specific selector extraction."""
        assert self.driver
        selectors = ["main", "article", ".content", "#content", ".post", ".entry"]
        for sel in selectors:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, sel)
                if el and len(el.text) > 50:
                    return el.text
            except:  # noqa: E722
                continue
        return ""

    def _rotate_cache_if_needed(self):
        """Rotate cache by deleting oldest files if cache size exceeds max_cache_files."""
        if self.max_cache_files is None:
            return
        
        cache_dir = "./datasets/url_cache"
        if not os.path.exists(cache_dir):
            return
        
        try:
            # Get all cache files with their modification times
            cache_files = []
            for filename in os.listdir(cache_dir):
                if filename.endswith(".json"):
                    filepath = os.path.join(cache_dir, filename)
                    mtime = os.path.getmtime(filepath)
                    cache_files.append((mtime, filepath))
            
            # If we're under the limit, nothing to do
            if len(cache_files) <= self.max_cache_files:
                return
            
            # Sort by modification time (oldest first)
            cache_files.sort()
            
            # Delete oldest files until we're under the limit
            files_to_delete = len(cache_files) - self.max_cache_files
            deleted = 0
            for mtime, filepath in cache_files[:files_to_delete]:
                try:
                    os.remove(filepath)
                    deleted += 1
                except Exception as e:
                    print(f"⚠️ Warning: Could not delete cache file {filepath}: {e}")
            
            if deleted > 0:
                print(f"🗑️  Cache rotation: Deleted {deleted} oldest cache files")
        except Exception as e:
            print(f"⚠️ Warning: Error during cache rotation: {e}")
