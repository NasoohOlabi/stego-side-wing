"""Generate claims-led statistical evidence reports from experiment summaries."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from services.statistical_claims_service import (  # noqa: E402
    DEFAULT_MANIFEST,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MD,
    load_summary_artifacts,
    write_claims_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate metrics/CLAIMS_REPORT.md and JSON.")
    parser.add_argument("summary", nargs="+", help="summary.json file or run directory")
    parser.add_argument("--output-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--output-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--accept-historical",
        action="store_true",
        help="Allow dirty/unknown provenance and label evidence as historical.",
    )
    args = parser.parse_args()
    artifacts = load_summary_artifacts([Path(value) for value in args.summary])
    if not artifacts:
        raise SystemExit("No summary artifacts found.")
    write_claims_report(
        artifacts,
        output_md=Path(args.output_md),
        output_json=Path(args.output_json),
        accept_historical=bool(args.accept_historical),
        manifest_path=Path(args.manifest),
    )


if __name__ == "__main__":
    main()
