"""Internal validation and HTTP helpers."""

from __future__ import annotations

from typing import Any

from .contracts import (
    CancellationToken,
    ProviderCapabilities,
    ProviderProgressCallback,
    ProviderProgressEvent,
    ProviderProgressStage,
    TranscriptionRequest,
)
from .errors import ProviderError, ProviderErrorKind, error_for_http_status
from .transport import HttpRequest, HttpTransport, decode_json_object


def validate_request(
    provider: str,
    capabilities: ProviderCapabilities,
    request: TranscriptionRequest,
) -> bytes:
    ensure_not_cancelled(provider, request.cancellation)
    if not capabilities.supports_language(request.language):
        raise ProviderError(
            provider,
            ProviderErrorKind.UNSUPPORTED_CAPABILITY,
            f"{provider} does not support language {request.language!r}",
        )
    if request.timestamps and not capabilities.timestamps:
        raise ProviderError(
            provider,
            ProviderErrorKind.UNSUPPORTED_CAPABILITY,
            f"{provider} does not support timestamps",
        )
    try:
        data = request.audio_path.read_bytes()
    except OSError as exc:
        raise ProviderError(
            provider,
            ProviderErrorKind.INVALID_AUDIO,
            "audio file could not be read",
        ) from exc
    if not data:
        raise ProviderError(
            provider,
            ProviderErrorKind.INVALID_AUDIO,
            "audio file is empty",
        )
    return data


def ensure_not_cancelled(provider: str, cancellation: CancellationToken | None) -> None:
    """Raise the stable cancellation error without exposing request content."""

    if cancellation is not None and cancellation.is_set():
        raise ProviderError(provider, ProviderErrorKind.CANCELLED, f"{provider} request cancelled")


def emit_progress(
    provider: str,
    callback: ProviderProgressCallback | None,
    stage: ProviderProgressStage,
    *,
    fraction: float | None = None,
    message: str | None = None,
) -> None:
    """Best-effort progress delivery; observers must not affect dictation."""

    if callback is None:
        return
    try:
        callback(ProviderProgressEvent(provider, stage, fraction, message))
    except Exception:
        # A UI observer can be torn down while a worker is completing.  The
        # provider result and its cleanup guarantees must remain deterministic.
        return


def send_json(
    provider: str,
    transport: HttpTransport,
    request: HttpRequest,
    *,
    cancellation: CancellationToken | None = None,
    progress: ProviderProgressCallback | None = None,
) -> dict[str, Any]:
    ensure_not_cancelled(provider, cancellation)
    emit_progress(provider, progress, ProviderProgressStage.REQUESTING)
    try:
        response = transport.send(request)
    except TimeoutError as exc:
        if cancellation is not None and cancellation.is_set():
            raise ProviderError(
                provider, ProviderErrorKind.CANCELLED, f"{provider} request cancelled"
            ) from exc
        raise ProviderError(
            provider,
            ProviderErrorKind.TIMEOUT,
            f"{provider} request timed out",
        ) from exc
    except (ConnectionError, OSError) as exc:
        raise ProviderError(
            provider,
            ProviderErrorKind.UNAVAILABLE,
            f"{provider} service is unavailable",
        ) from exc
    except Exception as exc:
        # A custom transport must not be able to leak a request body, URL
        # query, or credential through an adapter exception.
        raise ProviderError(
            provider,
            ProviderErrorKind.UNAVAILABLE,
            f"{provider} service is unavailable",
        ) from exc
    ensure_not_cancelled(provider, cancellation)
    if not 200 <= response.status < 300:
        raise error_for_http_status(provider, response.status)
    payload = decode_json_object(response.body)
    if payload is None:
        raise ProviderError(
            provider,
            ProviderErrorKind.MALFORMED_RESPONSE,
            f"{provider} returned an invalid response",
        )
    return payload


def require_text(provider: str, payload: dict[str, Any], key: str = "text") -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ProviderError(
            provider,
            ProviderErrorKind.MALFORMED_RESPONSE,
            f"{provider} response did not contain transcript text",
        )
    return value
