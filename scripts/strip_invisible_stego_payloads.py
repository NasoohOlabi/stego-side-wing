"""Strip legacy invisible stego payload suffixes from generated JSON artifacts."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.process_tracking import append_current_pid_to_log

LEGACY_INVISIBLE_CHARS = frozenset({"\u200c", "\u200d", "\u2060", "\u2063"})
STEGO_TEXT_KEYS = frozenset({"stegoText", "stego_text"})


def _strip_legacy_chars(text: str) -> tuple[str, Counter[str]]:
    removed: Counter[str] = Counter()
    kept: list[str] = []
    for char in text:
        if char in LEGACY_INVISIBLE_CHARS:
            name = unicodedata.name(char, "UNKNOWN")
            removed[f"U+{ord(char):04X} {name}"] += 1
            continue
        kept.append(char)
    return "".join(kept), removed


def _clean_json_value(value: Any) -> tuple[Any, Counter[str], int]:
    removed: Counter[str] = Counter()
    fields_changed = 0
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in STEGO_TEXT_KEYS and isinstance(item, str):
                new_text, item_removed = _strip_legacy_chars(item)
                cleaned[key] = new_text
                removed.update(item_removed)
                if new_text != item:
                    fields_changed += 1
                continue
            cleaned_item, item_removed, item_changed = _clean_json_value(item)
            cleaned[key] = cleaned_item
            removed.update(item_removed)
            fields_changed += item_changed
        return cleaned, removed, fields_changed
    if isinstance(value, list):
        cleaned_items = []
        for item in value:
            cleaned_item, item_removed, item_changed = _clean_json_value(item)
            cleaned_items.append(cleaned_item)
            removed.update(item_removed)
            fields_changed += item_changed
        return cleaned_items, removed, fields_changed
    return value, removed, fields_changed


def _artifact_paths(root: Path) -> list[Path]:
    roots = [root / "output-results", root / "metrics" / "e2e_runs"]
    paths: list[Path] = []
    for base in roots:
        if base.exists():
            paths.extend(path for path in base.rglob("*.json") if path.is_file())
    return sorted(paths)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing files.")
    parser.add_argument("--verbose", action="store_true", help="Print each changed file.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    total_removed: Counter[str] = Counter()
    changed_files = 0
    changed_fields = 0

    for path in _artifact_paths(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        cleaned, removed, fields = _clean_json_value(data)
        if not removed:
            continue
        changed_files += 1
        changed_fields += fields
        total_removed.update(removed)
        if args.verbose:
            print(f"{path}: fields={fields} removed={sum(removed.values())}")
        if not args.dry_run:
            path.write_text(
                json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    print(f"changed_files={changed_files}")
    print(f"changed_fields={changed_fields}")
    for name, count in total_removed.most_common():
        print(f"{name}: {count}")
    return 0


if __name__ == "__main__":
    append_current_pid_to_log()
    raise SystemExit(main())
