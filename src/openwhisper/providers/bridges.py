"""Adapters from provider contracts to the existing application-core protocols."""

from __future__ import annotations

from pathlib import Path

from openwhisper.core.models import Transcript, TranscriptSegment

from .contracts import (
    CancellationToken,
    CleanupContext,
    CleanupMode,
    CleanupProvider,
    CleanupRequest,
    ProviderProgressCallback,
    TranscriptionProvider,
    TranscriptionRequest,
)


class CoreTranscriptionAdapter:
    """Expose a request-based provider through ``core.TranscriptionProvider``."""

    def __init__(
        self,
        provider: TranscriptionProvider,
        *,
        language: str | None = None,
        prompt: str | None = None,
        timestamps: bool = False,
        recognition_hints: tuple[str, ...] = (),
        cancellation: CancellationToken | None = None,
        progress: ProviderProgressCallback | None = None,
    ) -> None:
        self.provider = provider
        self.name = provider.name
        self.model = provider.model
        self.capabilities = provider.capabilities
        self._language = _none_if_auto(language)
        self._prompt = prompt
        self._timestamps = timestamps
        self._recognition_hints = tuple(recognition_hints)
        self._cancellation = cancellation
        self._progress = progress

    def transcribe(self, audio_path: Path) -> Transcript:
        result = self.provider.transcribe(
            TranscriptionRequest(
                audio_path=Path(audio_path),
                language=self._language,
                prompt=self._prompt,
                timestamps=self._timestamps,
                recognition_hints=self._recognition_hints,
                cancellation=self._cancellation,
                progress=self._progress,
            )
        )
        return Transcript(
            text=result.text,
            language=result.language,
            provider=result.provider,
            duration_seconds=result.duration_seconds,
            segments=tuple(
                TranscriptSegment(
                    text=segment.text,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                )
                for segment in result.segments
            ),
        )


class CoreCleanupAdapter:
    """Expose a request-based provider through ``core.CleanupProvider``."""

    def __init__(
        self,
        provider: CleanupProvider,
        *,
        language_hint: str | None = None,
        context: CleanupContext | None = None,
        cancellation: CancellationToken | None = None,
        progress: ProviderProgressCallback | None = None,
    ) -> None:
        self.provider = provider
        self.name = provider.name
        self.model = provider.model
        self._language_hint = _none_if_auto(language_hint)
        self._context = context
        self._cancellation = cancellation
        self._progress = progress

    def cleanup(self, text: str, *, mode: str, custom_prompt: str | None = None) -> str:
        result = self.provider.cleanup(
            CleanupRequest(
                raw_text=text,
                mode=CleanupMode(mode),
                custom_instruction=custom_prompt,
                language_hint=self._language_hint,
                context=self._context,
                cancellation=self._cancellation,
                progress=self._progress,
            )
        )
        return result.text


def _none_if_auto(language: str | None) -> str | None:
    return None if language is None or language.strip().casefold() == "auto" else language.strip()
