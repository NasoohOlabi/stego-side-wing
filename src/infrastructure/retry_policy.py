"""Transport-error classification shared by every LLM caller.

These token sets answer one question: "did this exception come from the connection
dying, rather than from the server rejecting the request?" That answer does not depend
on which subsystem is calling, so it lives here instead of being copied per caller.

What is deliberately **not** shared is retry *tuning*. Each caller keeps its own attempt
count, backoff curve and retryable-status set, because they are set independently:

======================  ==========================  ==============================
                        workflows.adapters.llm      content_acquisition angle_runner
======================  ==========================  ==============================
env prefix              ``LLM_*``                   ``ANGLES_LLM_*``
max attempts            3                           6
backoff base / cap (s)  1.0 / 30.0                  1.5 / 60.0
retries HTTP 408        yes                         no
======================  ==========================  ==============================

Angle extraction sends much larger prompts and tolerates slower responses, so it is
tuned to wait longer and try more often. Collapsing the two would silently change both.
"""

from __future__ import annotations

# Exception class names (lower-cased, substring-matched) that indicate a transport fault.
RETRYABLE_TRANSPORT_NAME_TOKENS = frozenset(
    {
        "timeout",
        "connection",
        "connect",
        "chunked",
        "remoteprotocol",
        "protocolerror",
        "readerror",
        "writeerror",
        "disconnect",
    }
)

# Exception messages that indicate a transport fault even when the class name does not.
RETRYABLE_TRANSPORT_MESSAGE_TOKENS = frozenset(
    {
        "server disconnected without sending a response",
        "remote end closed connection without response",
        "connection reset by peer",
        "connection aborted",
        "broken pipe",
    }
)
