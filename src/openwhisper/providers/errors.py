"""Stable, non-sensitive errors for all provider implementations."""

from __future__ import annotations

from enum import StrEnum

from .redaction import redact_sensitive_text


class ProviderErrorKind(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    INVALID_AUDIO = "invalid_audio"
    MALFORMED_RESPONSE = "malformed_response"
    MODEL = "model"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CANCELLED = "cancelled"


_RETRYABLE = {
    ProviderErrorKind.RATE_LIMIT,
    ProviderErrorKind.TIMEOUT,
    ProviderErrorKind.UNAVAILABLE,
}


class ProviderError(RuntimeError):
    """An adapter failure safe to show in diagnostics.

    Adapter response bodies are deliberately never attached: providers can
    reflect request data, transcripts, or credentials in error payloads.
    """

    def __init__(
        self,
        provider: str,
        kind: ProviderErrorKind,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(redact_sensitive_text(message))
        self.provider = provider
        self.kind = kind
        self.status_code = status_code
        self.retryable = kind in _RETRYABLE


def error_for_http_status(provider: str, status: int) -> ProviderError:
    if status in {401, 403, 498}:
        kind = ProviderErrorKind.AUTHENTICATION
        message = f"{provider} rejected the configured credential"
    elif status == 429:
        kind = ProviderErrorKind.RATE_LIMIT
        message = f"{provider} rate limit reached"
    elif status in {408, 499, 504}:
        kind = ProviderErrorKind.TIMEOUT
        message = f"{provider} request timed out"
    elif status in {404}:
        kind = ProviderErrorKind.MODEL
        message = f"{provider} could not find or access the configured model"
    elif status in {400, 415, 422}:
        kind = ProviderErrorKind.INVALID_AUDIO
        message = f"{provider} rejected the audio request"
    else:
        kind = ProviderErrorKind.UNAVAILABLE
        message = f"{provider} service is unavailable"
    return ProviderError(provider, kind, message, status_code=status)
