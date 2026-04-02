"""Rewrite flat pipeline stego JSON files to n8n array shape under output-results.

Run from repo root::

    uv run python scripts/migrate_output_results_n8n_shape.py
    uv run python scripts/migrate_output_results_n8n_shape.py --apply
"""
from __future__ import annotations

import argparse
import sys
from fnmatch import fnmatch
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from workflows.utils.output_results_shape import migrate_output_results_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate flat stego artifacts (stego_text root dict) to n8n array shape."
    )
    parser.add_argument(
        "--dir",
        default="output-results",
        help="Directory to scan (relative to repo root if not absolute).",
    )
    parser.add_argument(
        "--pattern",
        default="*",
        help="fnmatch pattern against each file basename (default: all .json).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write migrated JSON; default is dry-run (report only).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print path for each non-ok outcome.",
    )
    args = parser.parse_args()

    root = Path(args.dir)
    if not root.is_absolute():
        root = (_REPO_ROOT / root).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    counts: dict[str, int] = {
        "ok": 0,
        "would_migrate": 0,
        "migrated": 0,
        "other": 0,
        "error": 0,
    }

    for path in sorted(root.rglob("*.json")):
        if not fnmatch(path.name, args.pattern):
            continue
        outcome = migrate_output_results_file(path, apply=args.apply)
        counts[outcome] += 1
        if args.verbose and outcome not in ("ok",):
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"{outcome}: {rel}")

    mode = "apply" if args.apply else "dry-run"
    print(f"output_results_n8n_shape ({mode}) root={root}")
    for k in ("ok", "would_migrate", "migrated", "other", "error"):
        print(f"  {k}: {counts[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
