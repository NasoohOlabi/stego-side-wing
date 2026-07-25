from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from services.zlg_comparison_service import (  # noqa: E402
    ComparisonInput,
    append_jsonl,
    run_comparison_sample,
)


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

    # ComparisonInput used to take `domain` and `comment_chain` separately; it now takes a
    # single `cover_texts` list that build_prompt normalizes and samples from. Both inputs
    # are context snippets, so they go in as two entries.
    cover_texts = [
        text
        for text in (_read_text(args.domain), _read_text(args.comment_chain))
        if text and text.strip()
    ]
    sample = ComparisonInput(
        cover_texts=cover_texts,
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
