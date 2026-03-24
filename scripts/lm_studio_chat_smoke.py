#!/usr/bin/env python3
"""
Minimal LM Studio OpenAI-compatible chat completion — same endpoint as angles.

Usage (from repo root):
  python scripts/lm_studio_chat_smoke.py
  python scripts/lm_studio_chat_smoke.py --model openai/gpt-oss-20b

Exits 0 on HTTP 200 with content; non-zero on failure. Helps reproduce
RemoteDisconnected vs 4xx/5xx vs timeouts without running the full pipeline.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Repo root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

from infrastructure.config import get_env, get_lm_studio_url  # noqa: E402

# Keep in sync with src/pipelines/angles/angle_runner.py defaults
DEFAULT_MODEL = "openai/gpt-oss-20b"
REQUEST_TIMEOUT = 120


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model id (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=REQUEST_TIMEOUT,
        help=f"HTTP timeout seconds (default: {REQUEST_TIMEOUT})",
    )
    args = parser.parse_args()

    base = get_lm_studio_url()
    endpoint = f"{base.rstrip('/')}/chat/completions"
    token = get_env("LM_STUDIO_API_TOKEN", "lm-studio") or "lm-studio"

    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You reply with one short sentence."},
            {"role": "user", "content": 'Say "pong" and nothing else.'},
        ],
        "temperature": 0,
        "max_tokens": 64,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    print(f"POST {endpoint}", file=sys.stderr)
    print(f"model={args.model!r} timeout={args.timeout}s", file=sys.stderr)

    try:
        r = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=args.timeout,
        )
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print(f"HTTP {r.status_code}", file=sys.stderr)
    try:
        data = r.json()
    except json.JSONDecodeError:
        print(r.text[:2000], file=sys.stderr)
        return 3

    if not r.ok:
        print(json.dumps(data, indent=2)[:4000], file=sys.stderr)
        return 4

    choices = data.get("choices") or []
    if not choices:
        print(json.dumps(data, indent=2)[:4000], file=sys.stderr)
        return 5

    content = choices[0].get("message", {}).get("content", "")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
