from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.zlg_comparison_service import ComparisonInput, append_jsonl, run_comparison_sample


def _read_text(value: str) -> str:
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one ZLG comparison sample against /hide + /reveal"
    )
    parser.add_argument("--domain", required=True, help="Domain text or path to a UTF-8 text file")
    parser.add_argument(
        "--comment-chain",
        required=True,
        help="Comment chain text or path to a UTF-8 text file",
    )
    parser.add_argument(
        "--target-payload",
        required=True,
        help="Target payload text or path to a UTF-8 text file",
    )
    parser.add_argument("--server-url", default="http://127.0.0.1:9000")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--no-reveal-check", action="store_true")
    parser.add_argument("--out-jsonl", default="logs/zlg_comparison.jsonl")
    args = parser.parse_args()

    sample = ComparisonInput(
        domain=_read_text(args.domain),
        comment_chain=_read_text(args.comment_chain),
        target_payload=_read_text(args.target_payload),
        server_url=args.server_url,
        max_retries=max(1, args.max_retries),
        do_reveal_check=not args.no_reveal_check,
    )
    result = run_comparison_sample(sample)
    append_jsonl(Path(args.out_jsonl), result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if bool(result.get("accepted")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
