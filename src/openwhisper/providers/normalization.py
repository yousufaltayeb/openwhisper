"""Provider-response parsing into the stable transcription contracts."""

from __future__ import annotations

import math
import wave
from collections.abc import Iterable
from pathlib import Path

from .contracts import TranscriptResult, TranscriptSegment
from .errors import ProviderError, ProviderErrorKind


def optional_number(value: object) -> float | None:
    """Convert finite numeric response fields, rejecting booleans and NaN."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_segments(
    provider: str,
    raw_segments: object,
    *,
    required: bool = False,
) -> tuple[TranscriptSegment, ...]:
    """Normalize standard ``{text,start,end}`` segment response objects."""

    if raw_segments is None:
        if required:
            raise _malformed(provider, "timestamp segments")
        return ()
    if not isinstance(raw_segments, list):
        raise _malformed(provider, "timestamp segments")

    normalized: list[TranscriptSegment] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise _malformed(provider, "timestamp segment")
        text = raw.get("text")
        start = optional_number(raw.get("start"))
        end = optional_number(raw.get("end"))
        if not isinstance(text, str) or start is None or end is None:
            raise _malformed(provider, "timestamp segment")
        confidence = optional_number(raw.get("confidence", raw.get("avg_logprob")))
        try:
            normalized.append(TranscriptSegment(text.strip(), start, end, confidence))
        except ValueError as exc:
            raise _malformed(provider, "timestamp segment") from exc
    return tuple(normalized)


def transcript_result(
    *,
    provider: str,
    model: str,
    text: object,
    language: object,
    duration_seconds: object = None,
    segments: Iterable[TranscriptSegment] = (),
) -> TranscriptResult:
    """Create a validated, normalized result from a provider response."""

    if not isinstance(text, str):
        raise _malformed(provider, "transcript text")
    duration = optional_number(duration_seconds)
    segment_tuple = tuple(segments)
    if duration is None:
        duration = max((segment.end_seconds for segment in segment_tuple), default=0.0)
    if duration < 0:
        raise _malformed(provider, "duration")
    return TranscriptResult(
        text=text,
        language=language if isinstance(language, str) and language.strip() else None,
        provider=provider,
        model=model,
        duration_seconds=duration,
        segments=segment_tuple,
    )


def wav_duration_seconds(path: Path) -> float:
    """Best-effort local duration for APIs that only return transcript text."""

    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            return audio.getnframes() / frame_rate if frame_rate else 0.0
    except (OSError, wave.Error):
        return 0.0


def _malformed(provider: str, expected: str) -> ProviderError:
    return ProviderError(
        provider,
        ProviderErrorKind.MALFORMED_RESPONSE,
        f"{provider} response did not contain valid {expected}",
    )
