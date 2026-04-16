"""State inspection and admin helpers for API routes."""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List

from infrastructure.config import METRICS_DIR, REPO_ROOT, STEPS, resolve_path

logger = logging.getLogger(__name__)


def safe_repo_path(relative_path: str) -> Path:
    if not relative_path:
        raise ValueError("path is required")
    normalized = relative_path[2:] if relative_path.startswith("./") else relative_path
    candidate = (REPO_ROOT / normalized).resolve()
    try:
        candidate.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("path must stay inside repository root") from exc
    return candidate


def get_paths_map() -> Dict[str, str]:
    paths: Dict[str, str] = {}
    for step_name, config in STEPS.items():
        paths[f"{step_name}.source_dir"] = str(resolve_path(config["source_dir"]))
        paths[f"{step_name}.dest_dir"] = str(resolve_path(config["dest_dir"]))
    paths["cache.flask"] = str(resolve_path("./cache-directory"))
    paths["cache.url"] = str(resolve_path("./datasets/url_cache"))
    paths["cache.angles"] = str(resolve_path("./datasets/angles_cache"))
    paths["db.kv"] = str(resolve_path("./kv_store.db"))
    paths["db.research_terms"] = str(resolve_path("./datasets/research_terms_cache.db"))
    paths["log.api"] = str(resolve_path("./logs/api.jsonl"))
    paths["log.prompts"] = str(resolve_path("./prompts.log"))
    paths["log.workflow_prompts_dir"] = str(resolve_path("./logs"))
    paths["log.workflow_prompts_glob"] = str(resolve_path("./logs/stego_prompts_*.log"))
    paths["metrics.dir"] = str(METRICS_DIR)
    paths["prompts.workflow_llm"] = str(resolve_path("./config/workflow_llm_prompts.json"))
    return paths


def list_directory(relative_path: str, recursive: bool = False, limit: int = 200) -> Dict[str, Any]:
    root = safe_repo_path(relative_path)
    if not root.exists():
        raise FileNotFoundError(f"path not found: {relative_path}")
    if not root.is_dir():
        raise ValueError("path must reference a directory")

    safe_limit = max(1, min(int(limit), 5000))
    entries: List[Dict[str, Any]] = []

    if recursive:
        iterator = root.rglob("*")
    else:
        iterator = root.iterdir()

    for item in iterator:
        if len(entries) >= safe_limit:
            break
        rel = item.resolve().relative_to(REPO_ROOT.resolve())
        size = item.stat().st_size if item.is_file() else None
        entries.append(
            {
                "path": str(rel).replace("\\", "/"),
                "name": item.name,
                "is_dir": item.is_dir(),
                "size_bytes": size,
            }
        )

    entries.sort(key=lambda x: (x["is_dir"], x["name"]))
    return {
        "base_path": str(root.relative_to(REPO_ROOT.resolve())).replace("\\", "/"),
        "recursive": recursive,
        "limit": safe_limit,
        "returned": len(entries),
        "items": entries,
    }


def read_json_file(relative_path: str) -> Dict[str, Any]:
    file_path = safe_repo_path(relative_path)
    if not file_path.exists():
        raise FileNotFoundError(f"path not found: {relative_path}")
    if not file_path.is_file():
        raise ValueError("path must reference a file")
    if file_path.suffix.lower() != ".json":
        raise ValueError("only .json files are supported by this endpoint")

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    rel = str(file_path.relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    logger.info(
        "state_read_json",
        extra={"event": "state", "action": "read_json", "path": rel},
    )
    return {
        "path": rel,
        "data": payload,
    }


def write_json_file(relative_path: str, data: Dict[str, Any], overwrite: bool = True) -> Dict[str, Any]:
    file_path = safe_repo_path(relative_path)
    if file_path.suffix.lower() != ".json":
        raise ValueError("only .json files are supported by this endpoint")
    if file_path.exists() and not overwrite:
        raise ValueError("target file already exists and overwrite is false")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    rel = str(file_path.relative_to(REPO_ROOT.resolve())).replace("\\", "/")
    logger.info(
        "state_write_json",
        extra={"event": "state", "action": "write_json", "path": rel},
    )
    return {
        "path": rel,
        "written": True,
    }


def delete_path(relative_path: str, recursive: bool = False) -> Dict[str, Any]:
    target = safe_repo_path(relative_path)
    if not target.exists():
        return {"deleted": False, "path": relative_path}
    if target.is_dir():
        if not recursive:
            raise ValueError("directory deletion requires recursive=true")
        shutil.rmtree(target)
    else:
        target.unlink()
    logger.info(
        "state_delete",
        extra={
            "event": "state",
            "action": "delete",
            "path": relative_path,
            "recursive": recursive,
        },
    )
    return {"deleted": True, "path": relative_path}


def clear_cache(target: str) -> Dict[str, Any]:
    targets = {
        "flask": resolve_path("./cache-directory"),
        "url": resolve_path("./datasets/url_cache"),
        "angles": resolve_path("./datasets/angles_cache"),
    }

    names = list(targets.keys()) if target == "all" else [target]
    if any(name not in targets for name in names):
        raise ValueError("target must be one of: flask, url, angles, all")

    cleared: Dict[str, int] = {}
    for name in names:
        cache_dir = targets[name]
        removed = 0
        if cache_dir.exists():
            for item in cache_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed += 1
        cleared[name] = removed

    logger.info(
        "state_clear_cache",
        extra={"event": "state", "action": "clear_cache", "target": target, "cleared": cleared},
    )
    return {"target": target, "cleared_entries": cleared}


def get_cache_stats() -> Dict[str, Any]:
    cache_dirs = {
        "flask": resolve_path("./cache-directory"),
        "url": resolve_path("./datasets/url_cache"),
        "angles": resolve_path("./datasets/angles_cache"),
    }
    stats: Dict[str, Any] = {}
    for name, directory in cache_dirs.items():
        files = 0
        size = 0
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_file():
                    files += 1
                    size += path.stat().st_size
        stats[name] = {
            "path": str(directory),
            "exists": directory.exists(),
            "files": files,
            "size_bytes": size,
        }
    return stats
