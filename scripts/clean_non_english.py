#!/usr/bin/env python3
"""
Script: clean_non_english.py
Purpose: Delete non-English .json files from dataset/javahelp and dataset/news in-place.
Usage:
  python3 scripts/clean_non_english.py
  python3 scripts/clean_non_english.py --in_dirs dataset/javahelp dataset/news
  python3 scripts/clean_non_english.py --init-missing
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable

from langdetect import DetectorFactory, detect

DetectorFactory.seed = 0


def _collect_json_files(root_dir: Path) -> list[Path]:
    return [p for p in root_dir.rglob("*.json") if p.is_file()]


def _is_english_text(text: str) -> bool:
    """
    Check if text is English. Returns True if English, False otherwise.
    Uses multiple heuristics:
    1. Check for non-ASCII characters (Devanagari, Arabic, Chinese, etc.)
    2. Use langdetect library for language detection
    """
    if not text or not text.strip():
        return False

    raw = "".join(ch if ch.isprintable() else " " for ch in text)
    sample = raw[:2000]

    if len(sample.strip()) < 20:
        return False

    # Check for non-ASCII characters that indicate non-English scripts
    # This catches Devanagari (Marathi, Hindi), Arabic, Chinese, etc.
    non_ascii_chars = [ch for ch in sample if ord(ch) > 127]
    if len(non_ascii_chars) > len(sample) * 0.3:  # More than 30% non-ASCII
        # Check if these are clearly non-Latin scripts
        # Devanagari range: U+0900-U+097F
        # Arabic range: U+0600-U+06FF
        # Chinese/Japanese/Korean: U+4E00-U+9FFF
        devanagari_range = range(0x0900, 0x0980)
        arabic_range = range(0x0600, 0x0700)
        cjk_range = range(0x4E00, 0xA000)

        for char in non_ascii_chars[:50]:  # Sample first 50 non-ASCII chars
            code_point = ord(char)
            if (
                code_point in devanagari_range
                or code_point in arabic_range
                or code_point in cjk_range
            ):
                return False

    # Use langdetect for language detection
    try:
        detected_lang = detect(sample)
        return detected_lang == "en"
    except Exception:
        # If langdetect fails, check if text is mostly ASCII
        # (English text is typically mostly ASCII)
        ascii_ratio = (
            sum(1 for ch in sample if ord(ch) < 128) / len(sample) if sample else 0
        )
        return ascii_ratio > 0.8  # More than 80% ASCII suggests English


def _extract_relevant_text(content: str) -> str:
    """
    Extract text content from JSON. Looks for common text fields
    like title, selftext, text, content, body, description, etc.
    """
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        # If JSON parsing fails, return empty string
        return ""

    collected: list[str] = []

    def collect(obj: object) -> None:
        if isinstance(obj, dict):
            # Check common text fields
            text_fields = (
                "title",
                "selftext",
                "text",
                "content",
                "body",
                "description",
                "summary",
                "name",
                "message",
                "comment",
                "post",
            )
            for field in text_fields:
                value = obj.get(field)
                if isinstance(value, str) and value.strip():
                    collected.append(value)
            # Also recursively check nested dictionaries
            for value in obj.values():
                collect(value)
        elif isinstance(obj, list):
            for item in obj:
                collect(item)
        elif isinstance(obj, str) and obj.strip() and len(obj) > 10:
            # If we encounter a standalone string that's substantial, include it
            collected.append(obj)

    collect(payload)
    return "\n".join(collected)


def _is_non_english_json(path: Path) -> bool:
    """
    Check if a JSON file contains non-English content.
    Returns True if non-English, False if English or cannot determine.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        # If we can't read the file, skip it (don't delete)
        return False

    relevant_text = _extract_relevant_text(content)
    if not relevant_text.strip():
        # If no text extracted, we can't determine language - keep the file
        return False

    is_english = _is_english_text(relevant_text)
    return not is_english


def _delete_files(paths: Iterable[Path]) -> int:
    deleted = 0
    for path in paths:
        path.unlink()
        deleted += 1
    return deleted


def main():
    parser = argparse.ArgumentParser(
        description="Remove .json files from the supplied dataset directories in-place."
    )
    parser.add_argument(
        "--in_dirs",
        nargs="+",
        default=["datasets/javahelp", "datasets/news"],
        help="Directories to scan for .json files.",
    )
    parser.add_argument(
        "--init-missing",
        action="store_true",
        help="Create missing input directories before attempting removal.",
    )
    args = parser.parse_args()

    in_dirs = [Path(p) for p in args.in_dirs]
    init_missing = getattr(args, "init_missing", False)
    total_deleted = 0

    for in_dir in in_dirs:
        if not in_dir.exists():
            if init_missing:
                in_dir.mkdir(parents=True, exist_ok=True)
                print(f"Created input directory: {in_dir}")
            else:
                print(f"Input directory not found: {in_dir}")
                # print the available files in the directory
                print(f"Available files: {os.listdir('.')}")
                continue

        json_files = _collect_json_files(in_dir)
        non_english = [p for p in json_files if _is_non_english_json(p)]
        deleted = _delete_files(non_english)
        total_deleted += deleted
        print(
            f"Removed {deleted} non-English .json file(s) "
            f"from {in_dir} (scanned {len(json_files)} files)."
        )

    print(f"Deletion complete: removed {total_deleted} total .json file(s).")


if __name__ == "__main__":
    main()
