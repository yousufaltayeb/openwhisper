"""Provider-neutral contracts used by the dictation pipeline."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class CancellationToken(Protocol):
    """Small cooperative-cancellation boundary shared by all providers.

    ``threading.Event`` satisfies this protocol, which keeps providers free of
    a GUI/event-loop dependency.  A provider checks it before starting work,
    between streamed chunks, and after a network request returns.
    """

    def is_set(self) -> bool: ...


class ProviderProgressStage(StrEnum):
    QUEUED = "queued"
    LOADING_MODEL = "loading_model"
    REQUESTING = "requesting"
    TRANSCRIBING = "transcribing"
    CLEANING = "cleaning"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ProviderProgressEvent:
    """A non-sensitive progress update emitted by a provider.

    ``message`` must always be a fixed status label: neither dictated text,
    request bodies, nor credentials belong in a progress event.
    """

    provider: str
    stage: ProviderProgressStage
    fraction: float | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.fraction is not None and not 0 <= self.fraction <= 1:
            raise ValueError("progress fraction must be between zero and one")


ProviderProgressCallback = Callable[[ProviderProgressEvent], None]


class ContextSource(StrEnum):
    APPLICATION = "application"
    SELECTED_TEXT = "selected_text"
    SURROUNDING_TEXT = "surrounding_text"
    RECENT_CLIPBOARD = "recent_clipboard"


@dataclass(frozen=True, slots=True)
class CleanupContextEntry:
    """One consented context source in a request-only editing prompt."""

    source: ContextSource
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", ContextSource(self.source))
        if not self.text.strip():
            raise ValueError("cleanup context cannot be blank")
        if len(self.text) > 16_000:
            raise ValueError("cleanup context exceeds the 16000 character limit")


@dataclass(frozen=True, slots=True)
class CleanupContext:
    """Explicit context supplied to an editing provider for one request.

    Context remains opt-in at the mode/desktop layer.  This value is
    deliberately request-scoped so a selected field or clipboard excerpt can
    never be retained by a provider, logger, or history record by accident.
    """

    source: ContextSource
    text: str
    application_name: str | None = None
    additional_entries: tuple[CleanupContextEntry, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", ContextSource(self.source))
        CleanupContextEntry(self.source, self.text)
        entries = tuple(self.additional_entries)
        if len(entries) > 3:
            raise ValueError("cleanup context supports at most four source entries")
        if any(entry.source is self.source for entry in entries):
            raise ValueError("cleanup context sources cannot be duplicated")
        object.__setattr__(self, "additional_entries", entries)
        if self.application_name is not None:
            label = self.application_name.strip()
            if len(label) > 256:
                raise ValueError("application context label exceeds the 256 character limit")
            object.__setattr__(self, "application_name", label or None)

    @property
    def entries(self) -> tuple[CleanupContextEntry, ...]:
        return (CleanupContextEntry(self.source, self.text), *self.additional_entries)

    @classmethod
    def from_content(
        cls,
        content: Mapping[object, str],
        *,
        application_name: str | None = None,
    ) -> CleanupContext | None:
        """Convert consent-filtered desktop content without importing core types.

        ``DictationContext.content_for`` returns a mapping keyed by its own
        context enum.  Accepting enum-like keys by value avoids a core/provider
        import cycle while keeping the policy decision entirely outside the
        provider layer.
        """

        entries = [
            CleanupContextEntry(ContextSource(getattr(source, "value", source)), text)
            for source, text in content.items()
            if isinstance(text, str) and text.strip()
        ]
        if not entries:
            return None
        first, *rest = entries
        return cls(first.source, first.text, application_name, tuple(rest))


class ConnectionTestStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    """Structured, safe-to-display result from a real minimal provider probe."""

    provider: str
    model: str
    status: ConnectionTestStatus
    message: str
    error_kind: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is ConnectionTestStatus.SUCCESS


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Features a provider promises to support.

    ``languages=None`` means the upstream service accepts arbitrary ISO/locale
    language hints. An explicit set is used for providers with a constrained
    language surface, such as Cohere Transcribe Arabic.
    """

    batch: bool
    streaming: bool
    languages: frozenset[str] | None
    timestamps: bool
    required_configuration: tuple[str, ...] = ()

    def supports_language(self, language: str | None) -> bool:
        """Return whether a user language hint can be sent to this provider.

        Language preferences arrive from a UI and may be either a primary
        language (``ar``), a locale (``ar-SA``), or ``auto``.  Provider
        capability declarations deliberately use primary, lower-case language
        tags, so locale hints remain useful without duplicating every locale.
        """

        if language is None or language.strip().casefold() == "auto" or self.languages is None:
            return True
        primary = language.strip().replace("_", "-").split("-", 1)[0].casefold()
        return primary in self.languages


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio_path: Path
    language: str | None = None
    prompt: str | None = None
    timestamps: bool = False
    recognition_hints: tuple[str, ...] = ()
    cancellation: CancellationToken | None = field(default=None, repr=False, compare=False)
    progress: ProviderProgressCallback | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "audio_path", Path(self.audio_path))
        hints = tuple(
            hint.strip()
            for hint in self.recognition_hints
            if isinstance(hint, str) and hint.strip()
        )
        if len(hints) > 100 or any(len(hint) > 160 for hint in hints):
            raise ValueError("recognition hints must contain at most 100 entries of 160 characters")
        object.__setattr__(self, "recognition_hints", hints)


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    text: str
    start_seconds: float
    end_seconds: float
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.start_seconds < 0 or self.end_seconds < self.start_seconds:
            raise ValueError("invalid transcript segment timestamps")


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    """Normalized result returned by every transcription implementation."""

    text: str
    language: str | None
    provider: str
    model: str
    duration_seconds: float
    segments: tuple[TranscriptSegment, ...] = ()

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model are required")
        object.__setattr__(self, "text", self.text.strip())
        object.__setattr__(self, "segments", tuple(self.segments))


class CleanupMode(StrEnum):
    RAW = "raw"
    CLEAN = "clean"
    FORMAL = "formal"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    raw_text: str
    mode: CleanupMode = CleanupMode.CLEAN
    custom_instruction: str | None = None
    language_hint: str | None = None
    context: CleanupContext | None = None
    cancellation: CancellationToken | None = field(default=None, repr=False, compare=False)
    progress: ProviderProgressCallback | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode is CleanupMode.CUSTOM and not (self.custom_instruction or "").strip():
            raise ValueError("custom cleanup mode requires an instruction")


@dataclass(frozen=True, slots=True)
class CleanupResult:
    text: str
    provider: str | None
    model: str | None
    used_fallback: bool = False
    warning: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", self.text.strip())


@runtime_checkable
class TranscriptionProvider(Protocol):
    name: str
    model: str
    capabilities: ProviderCapabilities

    def transcribe(self, request: TranscriptionRequest) -> TranscriptResult: ...

    def test_connection(self) -> ConnectionTestResult: ...


@runtime_checkable
class StreamingTranscriptionProvider(TranscriptionProvider, Protocol):
    def transcribe_stream(
        self, requests: Iterable[TranscriptionRequest]
    ) -> Iterator[TranscriptResult]: ...


@runtime_checkable
class CleanupProvider(Protocol):
    name: str
    model: str

    def cleanup(self, request: CleanupRequest) -> CleanupResult: ...

    def test_connection(self) -> ConnectionTestResult: ...
