"""Provider-neutral models and protocols used by the dictation core."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """A timestamped piece of a normalized transcription result."""

    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        if self.start_seconds < 0 or self.end_seconds < self.start_seconds:
            raise ValueError("invalid transcript segment timestamps")


@dataclass(frozen=True, slots=True)
class Transcript:
    """The common result returned by every transcription provider."""

    text: str
    language: str | None
    provider: str
    duration_seconds: float
    segments: tuple[TranscriptSegment, ...] = ()

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        object.__setattr__(self, "segments", tuple(self.segments))


@runtime_checkable
class TranscriptionProvider(Protocol):
    """Synchronous provider boundary; callers may run it in a worker thread."""

    def transcribe(self, audio_path: Path) -> Transcript:
        """Transcribe a complete audio file."""


@runtime_checkable
class CleanupProvider(Protocol):
    """Optional text cleanup boundary shared by local and cloud adapters."""

    @property
    def name(self) -> str:
        """Stable provider identifier stored with history."""

    def cleanup(
        self,
        text: str,
        *,
        mode: str,
        custom_prompt: str | None = None,
    ) -> str:
        """Return cleaned text without changing its intended meaning."""


def deduplicate_segments(segments: Sequence[TranscriptSegment]) -> str:
    """Merge overlapping provider segments without damaging code-switching.

    Streaming and chunked providers commonly repeat the tail of one segment at
    the start of the next. Comparison uses Unicode-aware case folding and strips
    punctuation, while output keeps the provider's original Arabic/Latin text.
    Deduplication is deliberately limited to segment boundaries so intentional
    repetitions inside a sentence remain untouched.
    """

    output: list[str] = []
    normalized: list[str] = []

    for segment in segments:
        words = segment.text.split()
        if not words:
            continue
        comparable = [_comparable_word(word) for word in words]
        overlap = _overlap_length(normalized, comparable)
        output.extend(words[overlap:])
        normalized.extend(comparable[overlap:])

    return " ".join(output).strip()


def _overlap_length(existing: Sequence[str], incoming: Sequence[str]) -> int:
    maximum = min(len(existing), len(incoming))
    for length in range(maximum, 0, -1):
        if existing[-length:] == incoming[:length]:
            return length
    return 0


def _comparable_word(word: str) -> str:
    # NFKD removes the distinction between precomposed and decomposed Latin
    # characters, and ignoring Arabic tashkeel makes repeated streaming tails
    # compare as equal without altering the text we return to the user.
    decomposed = unicodedata.normalize("NFKD", word.casefold())
    return "".join(
        character
        for character in decomposed
        if character.isalnum() and character != "ـ"  # Arabic tatweel
    )
