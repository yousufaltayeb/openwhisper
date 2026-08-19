from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openwhisper.core.audio import CapturedAudio, TempAudioManager
from openwhisper.core.history import HistoryRecord, SQLiteHistoryStore
from openwhisper.core.insertion import DesktopSession, DesktopTextInserter
from openwhisper.core.models import Transcript, TranscriptSegment, deduplicate_segments
from openwhisper.core.retention import AudioRetentionPolicy, RetainedAudioManager
from openwhisper.core.session import CleanupMode, DictationSession, SessionState


class Capture:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.started = 0
        self.cancelled = 0

    def start(self) -> None:
        self.started += 1
        self.path.write_bytes(b"audio")

    def stop(self) -> CapturedAudio:
        return CapturedAudio(self.path, duration_seconds=4.0)

    def cancel(self) -> None:
        self.cancelled += 1
        self.path.unlink(missing_ok=True)


class Provider:
    def __init__(self, transcript: Transcript, callback=None) -> None:
        self.transcript = transcript
        self.callback = callback

    def transcribe(self, _audio_path: Path) -> Transcript:
        if self.callback is not None:
            self.callback()
        return self.transcript


class BrokenCleanup:
    name = "cloud-cleanup"

    def cleanup(self, *_args, **_kwargs) -> str:
        raise RuntimeError("provider failed")


@dataclass
class MemoryHistory:
    records: list[HistoryRecord]

    def add(self, record: HistoryRecord) -> HistoryRecord:
        self.records.append(record)
        return record


class Clipboard:
    def __init__(self) -> None:
        self.value = ""

    def copy(self, text: str) -> None:
        self.value = text


def test_segment_deduplication_preserves_arabic_and_code_switching() -> None:
    result = deduplicate_segments(
        (
            TranscriptSegment(0, 1, "مرحبا بالعالم hello"),
            TranscriptSegment(0.8, 2, "بالعالم hello from OpenWhisper"),
        )
    )
    assert result == "مرحبا بالعالم hello from OpenWhisper"


def test_session_uses_raw_text_if_cleanup_fails_and_cleans_audio(tmp_path: Path) -> None:
    temporary_audio = TempAudioManager(tmp_path / "audio")
    path = temporary_audio.create_path()
    capture = Capture(path)
    clipboard = Clipboard()
    states: list[SessionState] = []
    history = MemoryHistory([])
    session = DictationSession(
        audio_capture=capture,
        temporary_audio=temporary_audio,
        transcription_provider=Provider(
            Transcript(
                text="unused",
                language="ar",
                provider="test",
                duration_seconds=0,
                segments=(
                    TranscriptSegment(0, 1, "مرحبا بالعالم"),
                    TranscriptSegment(0.9, 2, "بالعالم again"),
                ),
            )
        ),
        text_inserter=DesktopTextInserter(session=DesktopSession.UNKNOWN, clipboard=clipboard),
        history=history,
        cleanup_mode=CleanupMode.CLEAN,
        cleanup_provider=BrokenCleanup(),
        state_listener=states.append,
    )

    session.start_recording()
    outcome = session.stop_and_process()

    assert outcome.state is SessionState.COMPLETED
    assert outcome.raw_text == outcome.final_text == "مرحبا بالعالم again"
    assert outcome.warnings == ("Cleanup failed; the raw transcript was used.",)
    assert history.records[0].cleanup_provider == "cloud-cleanup"
    assert history.records[0].duration_seconds == 4.0
    assert clipboard.value == outcome.final_text
    assert not path.exists()
    assert states == [
        SessionState.RECORDING,
        SessionState.PROCESSING,
        SessionState.CLEANING,
        SessionState.INSERTING,
        SessionState.COMPLETED,
    ]


def test_session_supports_stopping_capture_before_provider_processing(tmp_path: Path) -> None:
    temporary_audio = TempAudioManager(tmp_path / "audio")
    path = temporary_audio.create_path()
    capture = Capture(path)
    session = DictationSession(
        audio_capture=capture,
        temporary_audio=temporary_audio,
        transcription_provider=Provider(Transcript("ready", "en", "test", 1)),
        text_inserter=DesktopTextInserter(
            session=DesktopSession.UNKNOWN,
            clipboard=Clipboard(),
        ),
        history=MemoryHistory([]),
    )

    session.start_recording()
    captured = session.stop_capture()

    assert session.state is SessionState.PROCESSING
    assert path.exists()
    outcome = session.process_captured(captured)
    assert outcome.state is SessionState.COMPLETED
    assert not path.exists()


def test_session_cancellation_during_transcription_suppresses_history_and_insert(
    tmp_path: Path,
) -> None:
    temporary_audio = TempAudioManager(tmp_path / "audio")
    path = temporary_audio.create_path()
    capture = Capture(path)
    clipboard = Clipboard()
    history = MemoryHistory([])
    transcript = Transcript("cancel me", "en", "test", 1)
    session: DictationSession
    session = DictationSession(
        audio_capture=capture,
        temporary_audio=temporary_audio,
        transcription_provider=Provider(transcript, callback=lambda: session.cancel()),
        text_inserter=DesktopTextInserter(session=DesktopSession.UNKNOWN, clipboard=clipboard),
        history=history,
    )

    session.start_recording()
    outcome = session.stop_and_process()

    assert outcome.state is SessionState.CANCELLED
    assert outcome.raw_text == "cancel me"
    assert history.records == []
    assert clipboard.value == ""
    assert not path.exists()


def test_cancelling_a_live_recording_stops_capture_immediately(tmp_path: Path) -> None:
    temporary_audio = TempAudioManager(tmp_path / "audio")
    path = temporary_audio.create_path()
    capture = Capture(path)
    session = DictationSession(
        audio_capture=capture,
        temporary_audio=temporary_audio,
        transcription_provider=Provider(Transcript("x", "en", "test", 1)),
        text_inserter=DesktopTextInserter(session=DesktopSession.UNKNOWN, clipboard=Clipboard()),
        history=MemoryHistory([]),
    )

    session.start_recording()
    session.cancel()

    assert capture.cancelled == 1
    assert session.state is SessionState.CANCELLED


def test_session_preserves_raw_text_and_applies_deterministic_personalization(
    tmp_path: Path,
) -> None:
    temporary_audio = TempAudioManager(tmp_path / "audio")
    capture = Capture(temporary_audio.create_path())
    history = MemoryHistory([])
    session = DictationSession(
        audio_capture=capture,
        temporary_audio=temporary_audio,
        transcription_provider=Provider(Transcript("open whisper", "en", "test", 1)),
        text_inserter=DesktopTextInserter(session=DesktopSession.UNKNOWN, clipboard=Clipboard()),
        history=history,
        mode_id="message",
        final_text_processor=lambda text: text.replace("open whisper", "OpenWhisper"),
    )

    session.start_recording()
    outcome = session.stop_and_process()

    assert outcome.raw_text == "open whisper"
    assert outcome.final_text == "OpenWhisper"
    assert history.records[0].mode_id == "message"
    assert history.records[0].latency_ms is not None


def test_session_retains_audio_only_when_enabled_and_links_it_to_history(tmp_path: Path) -> None:
    temporary_audio = TempAudioManager(tmp_path / "capture")
    capture = Capture(temporary_audio.create_path())
    history = SQLiteHistoryStore(
        tmp_path / "history.sqlite3", retained_audio_dir=tmp_path / "retained"
    )
    manager = RetainedAudioManager(
        temporary_audio.directory,
        tmp_path / "retained",
        AudioRetentionPolicy(enabled=True, days=7),
    )
    try:
        session = DictationSession(
            audio_capture=capture,
            temporary_audio=temporary_audio,
            transcription_provider=Provider(Transcript("recoverable", "en", "test", 1)),
            text_inserter=DesktopTextInserter(
                session=DesktopSession.UNKNOWN, clipboard=Clipboard()
            ),
            history=history,
            audio_retention=manager,
        )

        session.start_recording()
        outcome = session.stop_and_process()

        saved = history.get(outcome.history_record.id)
        assert saved.has_retained_audio
        assert saved.insertion_method == "clipboard"
        assert history.delete(saved.id)
        assert not saved.retained_audio_path.exists()
    finally:
        history.close()
