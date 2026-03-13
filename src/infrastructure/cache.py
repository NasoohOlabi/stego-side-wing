"""Shared caching utilities."""
import hashlib
import json
import os
from typing import Any, Optional


def deterministic_hash_sha256(input_string: str) -> str:
    """
    Hashes a string deterministically using SHA-256.
    The same input string will always produce the same hash.
    """
    encoded_string = input_string.encode("utf-8")
    hasher = hashlib.sha256()
    hasher.update(encoded_string)
    return hasher.hexdigest()


def read_json_cache(cache_file: str) -> Optional[Any]:
    """
    Read JSON from cache file if it exists.
    
    Args:
        cache_file: Path to cache file
        
    Returns:
        Cached data or None if not found/invalid
    """
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Error reading cache for {cache_file}: {e}")
    return None


def write_json_cache(cache_file: str, data: Any) -> None:
    """
    Write data to JSON cache file.
    
    Args:
        cache_file: Path to cache file
        data: Data to cache (must be JSON serializable)
    """
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Error saving cache for {cache_file}: {e}")
