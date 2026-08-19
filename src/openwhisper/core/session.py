"""Dictation-session state machine and failure-safe orchestration."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .audio import AudioCapture, CapturedAudio, TempAudioManager
from .history import HistoryRecord
from .insertion import DesktopTextInserter, InsertionResult
from .models import CleanupProvider, Transcript, TranscriptionProvider, deduplicate_segments
from .retention import RetainedAudio, RetainedAudioManager


class SessionState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    CLEANING = "cleaning"
    INSERTING = "inserting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CleanupMode(StrEnum):
    RAW = "raw"
    CLEAN = "clean"
    FORMAL = "formal"
    CUSTOM = "custom"


class SessionBusyError(RuntimeError):
    """Raised when a lifecycle action is invalid for the current session state."""


class HistorySink(Protocol):
    def add(self, record: HistoryRecord) -> HistoryRecord: ...


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    state: SessionState
    raw_text: str = ""
    final_text: str = ""
    transcript: Transcript | None = None
    insertion: InsertionResult | None = None
    inserted: bool = False
    copied: bool = False
    history_record: HistoryRecord | None = None
    warnings: tuple[str, ...] = ()


class DictationSession:
    """Orchestrate ``record -> transcribe -> clean -> insert`` synchronously.

    GUI callers normally run :meth:`stop_and_process` in a worker. Calling
    :meth:`cancel` while the worker is transcribing or cleaning prevents history
    and insertion once the in-flight provider call returns; provider SDK calls
    cannot safely be force-killed from another thread.
    """

    cleanup_fallback_warning = "Cleanup failed; the raw transcript was used."
    history_fallback_warning = "History could not be saved."

    def __init__(
        self,
        *,
        audio_capture: AudioCapture,
        temporary_audio: TempAudioManager,
        transcription_provider: TranscriptionProvider,
        text_inserter: DesktopTextInserter,
        history: HistorySink,
        cleanup_mode: CleanupMode | str = CleanupMode.RAW,
        cleanup_provider: CleanupProvider | None = None,
        custom_cleanup_prompt: str | None = None,
        mode_id: str = "raw",
        output_mode: str = "insert",
        final_text_processor: Callable[[str], str] | None = None,
        audio_retention: RetainedAudioManager | None = None,
        cancellation_event: threading.Event | None = None,
        state_listener: Callable[[SessionState], None] | None = None,
    ) -> None:
        cleanup_mode = CleanupMode(cleanup_mode)
        if cleanup_mode is CleanupMode.CUSTOM and not (custom_cleanup_prompt or "").strip():
            raise ValueError("custom cleanup mode requires a prompt")
        self.audio_capture = audio_capture
        self.temporary_audio = temporary_audio
        self.transcription_provider = transcription_provider
        self.text_inserter = text_inserter
        self.history = history
        self.cleanup_mode = cleanup_mode
        self.cleanup_provider = cleanup_provider
        self.custom_cleanup_prompt = custom_cleanup_prompt
        self.mode_id = mode_id
        if output_mode not in {"insert", "clipboard", "both"}:
            raise ValueError("unsupported output mode")
        self.output_mode = output_mode
        self.final_text_processor = final_text_processor
        self.audio_retention = audio_retention
        self._cancellation_event = cancellation_event or threading.Event()
        self._state_listener = state_listener
        self._state = SessionState.IDLE
        self._lock = threading.RLock()
        self._cancel_requested = False

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    @property
    def is_recording(self) -> bool:
        return self.state is SessionState.RECORDING

    def start_recording(self) -> None:
        """Start capture atomically with respect to a concurrent cancellation."""

        with self._lock:
            if self._state not in {
                SessionState.IDLE,
                SessionState.COMPLETED,
                SessionState.CANCELLED,
                SessionState.FAILED,
            }:
                raise SessionBusyError(f"session is {self._state.value}")
            self._cancel_requested = False
            self._cancellation_event.clear()
            try:
                # Keep the lock until the recorder is active. Otherwise a
                # simultaneous cancel can observe IDLE and be silently lost.
                self.audio_capture.start()
            except Exception as error:
                self._state = SessionState.FAILED
                start_error: Exception | None = error
            else:
                self._state = SessionState.RECORDING
                start_error = None
        self._notify_state(
            SessionState.FAILED if start_error is not None else SessionState.RECORDING
        )
        if start_error is not None:
            raise start_error

    def stop_and_process(self) -> SessionOutcome:
        """Stop the recorder and complete a single dictation transaction."""

        return self.process_captured(self.stop_capture())

    def stop_capture(self) -> CapturedAudio:
        """Stop capture before provider work, on the recorder's owning thread."""

        with self._lock:
            if self._state is not SessionState.RECORDING:
                raise SessionBusyError("session is not recording")
            self._state = SessionState.PROCESSING
        self._notify_state(SessionState.PROCESSING)

        try:
            return self.audio_capture.stop()
        except Exception:
            if self._is_cancelled():
                self._set_state(SessionState.CANCELLED)
            else:
                self._set_state(SessionState.FAILED)
            raise

    def process_captured(self, captured: CapturedAudio) -> SessionOutcome:
        """Complete provider, storage, and insertion work for stopped audio."""

        adopted = False
        with self._lock:
            if self._state is SessionState.IDLE:
                # A host-level provider retry can reuse already captured audio
                # without reopening the microphone. Ordinary callers still
                # transition through stop_capture first.
                self._state = SessionState.PROCESSING
                adopted = True
            if self._state is not SessionState.PROCESSING:
                raise SessionBusyError("session is not processing captured audio")
        if adopted:
            self._notify_state(SessionState.PROCESSING)

        retained_audio: RetainedAudio | None = None
        history_record: HistoryRecord | None = None
        processing_started = time.perf_counter()
        try:
            if self._is_cancelled():
                return self._cancelled_outcome()

            transcript = self.transcription_provider.transcribe(captured.path)
            raw_text = self._raw_text(transcript)
            if not raw_text:
                raise ValueError("transcription returned no text")
            if self._is_cancelled():
                return self._cancelled_outcome(raw_text=raw_text, transcript=transcript)

            final_text, warnings, cleanup_name = self._cleanup(raw_text)
            if self.final_text_processor is not None:
                processed = self.final_text_processor(final_text).strip()
                if processed:
                    final_text = processed
            if self._is_cancelled():
                return self._cancelled_outcome(
                    raw_text=raw_text, final_text=final_text, transcript=transcript
                )

            duration = transcript.duration_seconds
            if duration == 0 and captured.duration_seconds is not None:
                duration = captured.duration_seconds

            if self.audio_retention is not None:
                retained_audio = self.audio_retention.retain(captured.path)
            try:
                history_record = self.history.add(
                    HistoryRecord(
                        raw_text=raw_text,
                        final_text=final_text,
                        language=transcript.language,
                        transcription_provider=transcript.provider,
                        cleanup_provider=cleanup_name,
                        cleanup_model=self._provider_model(),
                        duration_seconds=duration,
                        latency_ms=round((time.perf_counter() - processing_started) * 1000),
                        mode_id=self.mode_id,
                        warning=" ".join(warnings) or None,
                        retained_audio_path=(
                            retained_audio.path if retained_audio is not None else None
                        ),
                        retained_audio_expires_at=(
                            retained_audio.expires_at if retained_audio is not None else None
                        ),
                    )
                )
            except Exception:
                if retained_audio is not None:
                    self.audio_retention.destroy(retained_audio.path)  # type: ignore[union-attr]
                    retained_audio = None
                # Dictation should remain useful if a local database becomes
                # unavailable. Do not expose provider text or exception data.
                warnings.append(self.history_fallback_warning)

            if self._is_cancelled():
                self._remove_history(history_record)
                return self._cancelled_outcome(
                    raw_text=raw_text, final_text=final_text, transcript=transcript
                )

            self._set_state(SessionState.INSERTING)
            if self._is_cancelled():
                self._remove_history(history_record)
                return self._cancelled_outcome(
                    raw_text=raw_text, final_text=final_text, transcript=transcript
                )
            insertion = self._insert_final_text(final_text)
            self._save_insertion_method(history_record, insertion)
            if insertion.warning:
                warnings.append(insertion.warning)
            self._set_state(SessionState.COMPLETED)
            return SessionOutcome(
                state=SessionState.COMPLETED,
                raw_text=raw_text,
                final_text=final_text,
                transcript=transcript,
                insertion=insertion,
                inserted=bool(insertion.inserted),
                copied=bool(insertion.copied),
                history_record=history_record,
                warnings=tuple(warnings),
            )
        except Exception:
            if self._is_cancelled():
                return self._cancelled_outcome()
            self._set_state(SessionState.FAILED)
            raise
        finally:
            self.temporary_audio.delete(captured.path)

    def cancel(self) -> None:
        """Cancel recording now, or suppress remaining work in a worker call."""

        with self._lock:
            current = self._state
            if current is SessionState.RECORDING:
                self._cancel_requested = True
                self._cancellation_event.set()
            elif current in {SessionState.PROCESSING, SessionState.CLEANING}:
                self._cancel_requested = True
                self._cancellation_event.set()
                return
            else:
                return
        try:
            self.audio_capture.cancel()
        finally:
            self._set_state(SessionState.CANCELLED)

    def _cleanup(self, raw_text: str) -> tuple[str, list[str], str | None]:
        if self.cleanup_mode is CleanupMode.RAW:
            return raw_text, [], None

        self._set_state(SessionState.CLEANING)
        if self.cleanup_provider is None:
            return raw_text, [self.cleanup_fallback_warning], None

        cleanup_name = self._provider_name()
        try:
            cleaned = self.cleanup_provider.cleanup(
                raw_text,
                mode=self.cleanup_mode.value,
                custom_prompt=self.custom_cleanup_prompt,
            ).strip()
        except Exception:
            return raw_text, [self.cleanup_fallback_warning], cleanup_name
        if not cleaned:
            return raw_text, [self.cleanup_fallback_warning], cleanup_name
        return cleaned, [], cleanup_name

    def _provider_name(self) -> str | None:
        if self.cleanup_provider is None:
            return None
        try:
            return self.cleanup_provider.name
        except Exception:
            return None

    def _provider_model(self) -> str | None:
        if self.cleanup_provider is None:
            return None
        try:
            value = getattr(self.cleanup_provider, "model", None)
            return str(value) if value else None
        except Exception:
            return None

    def _remove_history(self, record: HistoryRecord | None) -> None:
        if record is None or record.id is None:
            return
        delete = getattr(self.history, "delete", None)
        if callable(delete):
            try:
                delete(record.id)
            except Exception:
                pass

    def _save_insertion_method(
        self, record: HistoryRecord | None, insertion: InsertionResult
    ) -> None:
        if record is None or record.id is None:
            return
        update_delivery = getattr(self.history, "update_delivery", None)
        if callable(update_delivery):
            try:
                update_delivery(
                    record.id,
                    inserted=bool(insertion.inserted),
                    copied=bool(insertion.copied),
                    insertion_method=insertion.method.value,
                )
                return
            except Exception:
                pass
        update = getattr(self.history, "update_insertion_method", None)
        if callable(update):
            try:
                update(record.id, insertion.method.value)
            except Exception:
                pass

    def _insert_final_text(self, text: str) -> InsertionResult:
        """Call newer delivery-aware inserters while retaining old adapters."""

        insert = getattr(self.text_inserter, "insert")
        try:
            return insert(text, self.output_mode)
        except TypeError:
            # Provider/core test doubles from before output modes accepted only
            # the transcript argument. They retain the historical insert path.
            return insert(text)

    @staticmethod
    def _raw_text(transcript: Transcript) -> str:
        if transcript.segments:
            deduplicated = deduplicate_segments(transcript.segments)
            if deduplicated:
                return deduplicated.strip()
        return transcript.text.strip()

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancel_requested or self._cancellation_event.is_set()

    def _cancelled_outcome(
        self,
        *,
        raw_text: str = "",
        final_text: str = "",
        transcript: Transcript | None = None,
    ) -> SessionOutcome:
        self._set_state(SessionState.CANCELLED)
        return SessionOutcome(
            state=SessionState.CANCELLED,
            raw_text=raw_text,
            final_text=final_text,
            transcript=transcript,
        )

    def _set_state(self, state: SessionState) -> None:
        with self._lock:
            self._state = state
        self._notify_state(state)

    def _notify_state(self, state: SessionState) -> None:
        if self._state_listener is not None:
            try:
                self._state_listener(state)
            except Exception:
                # UI observers must not break audio cleanup or session recovery.
                pass
