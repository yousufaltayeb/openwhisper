"""Helpers for keeping credentials and dictated text out of diagnostics."""

from __future__ import annotations

import re
from collections.abc import Iterable

_BEARER = re.compile(r"(?i)(bearer|token)\s+[a-z0-9_\-.]{8,}")
_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|authorization|token|secret)\s*[:=]\s*[^\s,;]+")
_URL_CREDENTIAL = re.compile(r"(?i)([?&](?:api[_-]?key|token|key)=)[^&#\s]+")


def redact_sensitive_text(value: object, *, secrets: Iterable[str] = ()) -> str:
    """Return a compact diagnostic string without known credential material.

    Providers can reflect request metadata in error responses.  Adapters do
    not expose response bodies, and this second line of defence sanitizes
    local-library exception messages before an error is allowed to leave the
    provider layer.  It deliberately does not attempt to preserve dictated
    text: callers should use fixed errors for request failures.
    """

    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = _BEARER.sub(r"\1 [REDACTED]", text)
    text = _ASSIGNMENT.sub(r"\1=[REDACTED]", text)
    text = _URL_CREDENTIAL.sub(r"\1[REDACTED]", text)
    return text[:500]
