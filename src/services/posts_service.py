"""Posts management service."""
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from infrastructure.config import STEPS

logger = logging.getLogger(__name__)

_LIST_CACHE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}


def is_file_in_folder(folder_path: str, file_name: str) -> bool:
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


def list_posts(count: int, step: str, tag: Optional[str] = None, offset: int = 0) -> Dict[str, List[str]]:
    """
    List posts from a step directory.
    
    Args:
        count: Number of posts to return
        step: Step name (must be in STEPS)
        tag: Optional tag to filter by
        offset: Offset for pagination
        
    Returns:
        Dict with 'fileNames' key containing list of filenames
        
    Raises:
        FileNotFoundError: If source directory doesn't exist
        ValueError: If step is invalid or no files found
    """
    if step not in STEPS:
        raise ValueError(f"Invalid step: {step}")
    
    src_dir = STEPS[step]["source_dir"]
    dest_dir = STEPS[step]["dest_dir"]
    
    if not os.path.isdir(src_dir):
        raise FileNotFoundError(
            f"Post directory not found: {src_dir}. Please run data_nesting_script.py first."
        )
    os.makedirs(dest_dir, exist_ok=True)
    json_files = _get_unprocessed_sorted_files(src_dir=src_dir, dest_dir=dest_dir, tag=tag)

    if not json_files:
        raise ValueError(
            f"No JSON post files found in {src_dir}. Check your data processing output."
        )

    # Sort files by size (descending: largest first)
    out = json_files[offset : offset + count]
    logger.info(
        "list_posts",
        extra={
            "event": "posts",
            "action": "list",
            "step": step,
            "count_requested": count,
            "returned": len(out),
            "offset": offset,
            "tag": tag,
        },
    )
    return {"fileNames": out}


def _get_unprocessed_sorted_files(src_dir: str, dest_dir: str, tag: Optional[str]) -> List[str]:
    """Return cached list of source JSON files not yet present in destination."""
    tag_suffix = f"_{tag}" if tag else ""
    cache_key = (src_dir, dest_dir, tag_suffix)
    src_mtime = os.stat(src_dir).st_mtime_ns
    dest_mtime = os.stat(dest_dir).st_mtime_ns
    cached = _LIST_CACHE.get(cache_key)
    if cached and cached["src_mtime"] == src_mtime and cached["dest_mtime"] == dest_mtime:
        return list(cached["files"])

    dest_files = {entry.name for entry in os.scandir(dest_dir) if entry.is_file()}
    candidates: List[Tuple[str, int]] = []
    for entry in os.scandir(src_dir):
        if not entry.is_file() or not entry.name.endswith(".json"):
            continue
        dest_name = f"{entry.name[:-5]}{tag_suffix}.json"
        if dest_name in dest_files:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        candidates.append((entry.name, size))

    candidates.sort(key=lambda item: item[1], reverse=True)
    ordered = [name for name, _ in candidates]
    _LIST_CACHE[cache_key] = {
        "src_mtime": src_mtime,
        "dest_mtime": dest_mtime,
        "files": ordered,
    }
    return ordered


def get_post(post: str, step: str) -> Dict[str, Any]:
    """
    Get a single post by filename.
    
    Args:
        post: Post filename
        step: Step name (must be in STEPS)
        
    Returns:
        Post data as dict
        
    Raises:
        ValueError: If step is invalid
        FileNotFoundError: If post file doesn't exist
    """
    if step not in STEPS:
        raise ValueError(f"Invalid step: {step}")
    
    src_dir = STEPS[step]["source_dir"]
    file_path = os.path.join(src_dir, post)
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Post file not found: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info(
        "get_post",
        extra={"event": "posts", "action": "get", "step": step, "post": post},
    )
    return data


def save_post(post_data: Dict[str, Any], step: str) -> Dict[str, Any]:
    """
    Save a post to the step's destination directory.
    
    Args:
        post_data: Post data dict (must include 'id' field)
        step: Step name (must be in STEPS)
        
    Returns:
        Dict with success info including filename and path
        
    Raises:
        ValueError: If step is invalid or post missing 'id'
    """
    if step not in STEPS:
        raise ValueError(f"Invalid step: {step}")
    
    post_id = post_data.get("id")
    if not post_id:
        raise ValueError("Post must include 'id' field")
    
    dest_dir = STEPS[step]["dest_dir"]
    os.makedirs(dest_dir, exist_ok=True)
    dest_file_path = os.path.join(dest_dir, f"{post_id}.json")

    with open(dest_file_path, "w", encoding="utf-8") as f:
        json.dump(post_data, f, indent=2, ensure_ascii=False)

    logger.info(
        "save_post",
        extra={"event": "posts", "action": "save", "step": step, "post_id": post_id},
    )
    return {
        "success": True,
        "filename": f"{post_id}.json",
        "path": dest_file_path,
    }


def save_object(data: Dict[str, Any], step: str, filename: str) -> Dict[str, Any]:
    """
    Save arbitrary object to step's destination directory.
    
    Args:
        data: Data to save
        step: Step name (must be in STEPS)
        filename: Filename (must not contain directory separators)
        
    Returns:
        Dict with success info
        
    Raises:
        ValueError: If step is invalid or filename contains separators
    """
    if step not in STEPS:
        raise ValueError(f"Invalid step: {step}")
    
    if os.path.basename(filename) != filename:
        raise ValueError("'filename' must not contain directory separators")
    
    dest_dir = STEPS[step]["dest_dir"]
    os.makedirs(dest_dir, exist_ok=True)
    dest_file_path = os.path.join(dest_dir, filename)

    with open(dest_file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(
        "save_object",
        extra={"event": "posts", "action": "save_object", "step": step, "file_name": filename},
    )
    return {"success": True, "filename": filename, "path": dest_file_path}
