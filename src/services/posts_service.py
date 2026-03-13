"""Posts management service."""
import json
import os
from typing import Any, Dict, List, Optional

from infrastructure.config import STEPS


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
    
    try:
        all_files = os.listdir(src_dir)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Post directory not found: {src_dir}. Please run data_nesting_script.py first."
        )

    json_files = [
        f
        for f in all_files
        if f.endswith(".json")
        and (
            not is_file_in_folder(
                dest_dir,
                f[:-5]
                + str("_" + tag if (tag is not None) and (tag != "") else "")
                + ".json",
            )
        )
    ]

    if not json_files:
        raise ValueError(
            f"No JSON post files found in {src_dir}. Check your data processing output."
        )

    # Sort files by size (descending: largest first)
    json_files.sort(
        key=lambda f: os.path.getsize(os.path.join(src_dir, f)), reverse=True
    )

    return {"fileNames": json_files[offset: offset + count]}


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
        return json.load(f)


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

    return {"success": True, "filename": filename, "path": dest_file_path}
