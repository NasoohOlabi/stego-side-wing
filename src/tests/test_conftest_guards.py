"""Tests for the shared test guards themselves.

If ``block_external_network`` silently stopped working, the suite would go back to being
able to make real API calls during a refactor without anyone noticing.
"""

from __future__ import annotations

import socket

import pytest


def test_external_connections_are_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError, match="Blocked outbound connection"):
            sock.connect(("example.com", 80))
    finally:
        sock.close()


def test_loopback_connections_are_not_blocked() -> None:
    """Loopback must stay reachable so local-backend fallbacks keep their real behaviour."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(("127.0.0.1", port))
    finally:
        client.close()
        server.close()
