"""Safe, real connection probes for provider settings.

The probe material is generated locally and contains no microphone capture,
clipboard, selected text, context, or credentials.  It deliberately uses the
same request path as a real dictation so authentication, model access, rate
limits, timeouts, and response parsing are verified rather than inferred from
configuration alone.
"""

from __future__ import annotations

import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import ConnectionTestResult, ConnectionTestStatus
from .errors import ProviderError, ProviderErrorKind


def connection_result(
    provider: str,
    model: str,
    probe: Callable[[], object],
) -> ConnectionTestResult:
    """Run a probe and turn every expected provider failure into typed output."""

    try:
        probe()
    except ProviderError as exc:
        return ConnectionTestResult(
            provider=provider,
            model=model,
            status=ConnectionTestStatus.FAILED,
            message=str(exc),
            error_kind=exc.kind.value,
        )
    except Exception:
        # Never pass a third-party exception through settings: it can include
        # an Authorization header or a request body.
        return ConnectionTestResult(
            provider=provider,
            model=model,
            status=ConnectionTestStatus.FAILED,
            message=f"{provider} connection test could not be completed",
            error_kind=ProviderErrorKind.UNAVAILABLE.value,
        )
    return ConnectionTestResult(
        provider=provider,
        model=model,
        status=ConnectionTestStatus.SUCCESS,
        message="Connection verified with a minimal no-user-content request.",
    )


@contextmanager
def minimal_silence_wav() -> Iterator[Path]:
    """Yield a short valid WAV containing silence, then reliably remove it."""

    with TemporaryDirectory(prefix="openwhisper-connection-") as directory:
        path = Path(directory) / "probe.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            # One second is long enough for providers that validate duration,
            # yet contains no spoken or user-derived material.
            output.writeframes(b"\x00\x00" * 16_000)
        yield path
