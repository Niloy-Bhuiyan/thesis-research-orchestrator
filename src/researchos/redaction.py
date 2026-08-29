"""Scrub credentials out of text before it is stored or transmitted.

Error messages from HTTP clients routinely embed the request URL, and some APIs
put the credential in the path rather than a header. Telegram is the obvious
one: every call is https://api.telegram.org/bot<TOKEN>/method, so any connection
failure produces an exception string containing the bot token.

Anything written to the event stream is therefore scrubbed here first. The event
stream is synced to the coordination layer and rendered in a browser, so a
secret that reaches it has left the machine.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[redacted]"

_PATTERNS = [
    # Telegram bot token, in a URL path or on its own.
    re.compile(r"/bot\d{6,12}:[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"\b\d{6,12}:AA[A-Za-z0-9_-]{20,}"),
    # Supabase personal access token and JWTs (anon and service role keys).
    re.compile(r"\bsbp_[A-Za-z0-9]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # GitHub tokens.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    # OpenAI and Anthropic style keys, in case paid fallback is ever enabled.
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    # Credentials passed as query parameters.
    re.compile(r"(?i)\b(apikey|api_key|access_token|token)=[A-Za-z0-9._-]{8,}"),
    # Authorization headers echoed into a message.
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{20,}"),
]


def redact(text: str | None) -> str | None:
    """Replace anything that looks like a credential.

    Deliberately pattern-based rather than value-based: a message can contain a
    secret this process never loaded, such as one embedded in a URL by a
    library, so matching only against known values would miss it.
    """
    if not text:
        return text
    cleaned = text
    for pattern in _PATTERNS:
        cleaned = pattern.sub(
            lambda m: ("/bot" + PLACEHOLDER)
            if m.group(0).lower().startswith("/bot")
            else PLACEHOLDER,
            cleaned,
        )
    return cleaned


def contains_secret(text: str | None) -> bool:
    """Whether redaction would change the text. Used by tests and purges."""
    return bool(text) and redact(text) != text
