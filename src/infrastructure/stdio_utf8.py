"""Normalize stdout/stderr for UTF-8 (avoids cp1252 UnicodeEncodeError on Windows)."""

from __future__ import annotations

import contextlib
import os
import sys


def configure_stdio_utf8() -> None:
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            with contextlib.suppress(OSError, ValueError, AttributeError):
                reconf(encoding="utf-8", errors="replace")
