import argparse
import json
from pathlib import Path
from typing import Any


EMPTY_VALUES = (None, "", [], {})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy search_results from datasets/news_researched to datasets/news_angles "
            "only when news_angles is missing/empty."
        )
    )
    parser.add_argument(
        "--angles-dir",
        default="datasets/news_angles",
        help="Directory containing angle JSON files.",
    )
    parser.add_argument(
        "--researched-dir",
        default="datasets/news_researched",
        help="Directory containing researched JSON files.",
    )
    parser.add_argument(
        "--post-id",
        default=None,
        help="Optional post id (without .json) to process only one file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def get_data_container(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    if isinstance(payload, dict):
        return payload
    return None


def is_missing_or_empty_search_results(value: Any) -> bool:
    return value in EMPTY_VALUES


def has_non_empty_search_results(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return len(value) > 0
    return value not in (None, "")


def iter_target_files(angles_dir: Path, post_id: str | None) -> list[Path]:
    if post_id:
        return [angles_dir / f"{post_id}.json"]
    return sorted(p for p in angles_dir.glob("*.json") if p.is_file())


def main() -> None:
    args = parse_args()
    angles_dir = Path(args.angles_dir)
    researched_dir = Path(args.researched_dir)

    if not angles_dir.exists():
        raise FileNotFoundError(f"angles_dir not found: {angles_dir}")
    if not researched_dir.exists():
        raise FileNotFoundError(f"researched_dir not found: {researched_dir}")

    scanned = 0
    updated = 0
    skipped_missing_source = 0
    skipped_no_source_results = 0
    skipped_target_already_has_results = 0
    skipped_invalid_json = 0

    for angle_path in iter_target_files(angles_dir, args.post_id):
        scanned += 1

        if not angle_path.exists():
            print(f"SKIP missing angle file: {angle_path}")
            continue

        source_path = researched_dir / angle_path.name
        if not source_path.exists():
            skipped_missing_source += 1
            continue

        try:
            angle_payload = load_json(angle_path)
            source_payload = load_json(source_path)
        except (json.JSONDecodeError, OSError) as exc:
            skipped_invalid_json += 1
            print(f"SKIP invalid JSON/read error for {angle_path.name}: {exc}")
            continue

        angle_data = get_data_container(angle_payload)
        source_data = get_data_container(source_payload)
        if angle_data is None or source_data is None:
            skipped_invalid_json += 1
            print(f"SKIP unsupported JSON shape in {angle_path.name}")
            continue

        target_results = angle_data.get("search_results")
        if not is_missing_or_empty_search_results(target_results):
            skipped_target_already_has_results += 1
            continue

        source_results = source_data.get("search_results")
        if not has_non_empty_search_results(source_results):
            skipped_no_source_results += 1
            continue

        angle_data["search_results"] = source_results
        updated += 1
        print(f"{'DRY-RUN would update' if args.dry_run else 'UPDATED'} {angle_path.name}")

        if not args.dry_run:
            save_json(angle_path, angle_payload)

    print("\nDone.")
    print(f"- scanned: {scanned}")
    print(f"- updated: {updated}")
    print(f"- skipped (missing source file): {skipped_missing_source}")
    print(f"- skipped (source missing/empty search_results): {skipped_no_source_results}")
    print(f"- skipped (target already has search_results): {skipped_target_already_has_results}")
    print(f"- skipped (invalid JSON/shape/read error): {skipped_invalid_json}")


if __name__ == "__main__":
    main()
