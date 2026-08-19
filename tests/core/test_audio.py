from __future__ import annotations

import subprocess
import wave
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest

from openwhisper.core.audio import (
    AudioCaptureConfig,
    AudioDeviceError,
    BufferedParecAudioCapture,
    ParecAudioCapture,
    QtMultimediaAudioCapture,
    TempAudioManager,
)


class FakeProcess:
    def __init__(self, *, timeout_once: bool = False) -> None:
        self.terminated = 0
        self.killed = 0
        self.waited = 0
        self.timeout_once = timeout_once

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1

    def wait(self, *, timeout: float) -> None:
        self.waited += 1
        if self.timeout_once and self.waited == 1:
            raise subprocess.TimeoutExpired("parec", timeout)


class RawAudioProcess(FakeProcess):
    def __init__(self, pcm: bytes) -> None:
        super().__init__()
        self.stdout = BytesIO(pcm)


class FakeQtStream:
    def bytesAvailable(self) -> int:
        return 0


class FakeQtAudioSource:
    def __init__(self, *, error: object = 0) -> None:
        self._error = error
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1

    def error(self) -> object:
        return self._error


def test_temp_manager_only_deletes_its_direct_children(tmp_path: Path) -> None:
    manager = TempAudioManager(tmp_path / "audio")
    managed = manager.create_path()
    outside = tmp_path / "outside.wav"
    outside.write_text("keep", encoding="utf-8")

    manager.delete(managed)
    assert not managed.exists()

    with pytest.raises(ValueError):
        manager.delete(outside)
    assert outside.read_text(encoding="utf-8") == "keep"

    escaped = manager.directory / ".." / outside.name
    with pytest.raises(ValueError):
        manager.delete(escaped)
    assert outside.exists()


def test_temp_manager_removes_only_stale_owned_audio(tmp_path: Path) -> None:
    manager = TempAudioManager(tmp_path / "audio")
    stale = manager.create_path()
    fresh = manager.create_path()
    stale.touch()
    fresh.touch()
    stale_time = 1_000.0
    fresh_time = 2_000.0
    import os

    os.utime(stale, (stale_time, stale_time))
    os.utime(fresh, (fresh_time, fresh_time))

    assert manager.cleanup_stale(max_age=timedelta(seconds=100), now=1_500) == 1
    assert not stale.exists()
    assert fresh.exists()


def test_parec_stop_kills_a_hung_recorder_and_returns_capture(tmp_path: Path) -> None:
    manager = TempAudioManager(tmp_path / "audio")
    process = FakeProcess(timeout_once=True)
    ticks = iter((10.0, 12.5))
    capture = ParecAudioCapture(
        manager,
        process_factory=lambda *_args, **_kwargs: process,
        monotonic=lambda: next(ticks),
    )

    capture.start()
    captured = capture.stop()

    assert captured.path.exists()
    assert captured.duration_seconds == 2.5
    assert process.terminated == 1
    assert process.killed == 1
    assert process.waited == 2
    manager.delete(captured.path)


def test_parec_cancel_and_start_failure_cleanup_temp_files(tmp_path: Path) -> None:
    manager = TempAudioManager(tmp_path / "audio")
    process = FakeProcess()
    capture = ParecAudioCapture(
        manager,
        process_factory=lambda *_args, **_kwargs: process,
    )
    capture.start()
    capture.cancel()
    assert list(manager.directory.iterdir()) == []

    failing = ParecAudioCapture(
        manager,
        process_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no parec")),
    )
    with pytest.raises(OSError, match="no parec"):
        failing.start()
    assert list(manager.directory.iterdir()) == []


def test_buffered_parec_publishes_safe_non_overlapping_wav_chunks(tmp_path: Path) -> None:
    manager = TempAudioManager(tmp_path / "audio")
    # Four 16-bit mono frames at 16 Hz makes duration and WAV metadata easy to
    # verify while exercising raw PCM capture rather than a recorder-written WAV.
    pcm = b"\x01\x00\x02\x00\x03\x00\x04\x00"
    process = RawAudioProcess(pcm)
    capture = BufferedParecAudioCapture(
        manager,
        process_factory=lambda *_args, **_kwargs: process,
        command_factory=lambda: ("parec",),
        sample_rate=16,
        read_size=2,
    )

    capture.start()
    assert capture._reader is not None  # The reader owns the live pipe.
    capture._reader.join(timeout=1)
    first_chunk = capture.take_chunk()
    assert first_chunk is not None
    assert capture.take_chunk() is None
    snapshot = capture.snapshot()
    assert snapshot is not None
    complete, tail = capture.stop_with_final_chunk()

    assert tail is None
    assert first_chunk.duration_seconds == 0.25
    with wave.open(str(first_chunk.path), "rb") as output:
        assert output.getnchannels() == 1
        assert output.getsampwidth() == 2
        assert output.getframerate() == 16
        assert output.readframes(output.getnframes()) == pcm
    with wave.open(str(snapshot.path), "rb") as output:
        assert output.readframes(output.getnframes()) == pcm
    with wave.open(str(complete.path), "rb") as output:
        assert output.readframes(output.getnframes()) == pcm

    for captured in (first_chunk, snapshot, complete):
        manager.delete(captured.path)
    assert process.terminated == 1


def test_buffered_parec_returns_unclaimed_final_tail(tmp_path: Path) -> None:
    manager = TempAudioManager(tmp_path / "audio")
    pcm = b"\x00\x00" * 8
    process = RawAudioProcess(pcm)
    capture = BufferedParecAudioCapture(
        manager,
        process_factory=lambda *_args, **_kwargs: process,
        command_factory=lambda: ("parec",),
        sample_rate=16,
    )

    capture.start()
    assert capture._reader is not None
    capture._reader.join(timeout=1)
    complete, final_chunk = capture.stop_with_final_chunk()

    assert final_chunk is not None
    assert final_chunk.duration_seconds == complete.duration_seconds == 0.5
    with wave.open(str(final_chunk.path), "rb") as output:
        assert output.readframes(output.getnframes()) == pcm
    manager.delete(complete.path)
    manager.delete(final_chunk.path)


def test_qt_capture_silence_trim_preserves_only_non_quiet_pcm() -> None:
    config = AudioCaptureConfig(sample_rate=16, silence_threshold=5)
    pcm = b"\x00\x00\x03\x00\x06\x00\x00\x00"

    assert QtMultimediaAudioCapture._trim_silence(pcm, config) == b"\x06\x00"


def test_qt_capture_rejects_an_empty_backend_before_model_work(tmp_path: Path) -> None:
    manager = TempAudioManager(tmp_path / "audio")
    capture = QtMultimediaAudioCapture(manager)
    source = FakeQtAudioSource()
    capture._source = source
    capture._stream = FakeQtStream()
    capture._config = AudioCaptureConfig()

    with pytest.raises(AudioDeviceError, match="no microphone audio"):
        capture.stop()

    assert source.stopped == 1
    assert not capture.is_recording
    assert not manager.directory.exists()


def test_qt_capture_surfaces_backend_failure_for_silent_pcm(tmp_path: Path) -> None:
    class OpenError:
        name = "OpenError"
        value = 1

    manager = TempAudioManager(tmp_path / "audio")
    capture = QtMultimediaAudioCapture(manager)
    capture._source = FakeQtAudioSource(error=OpenError())
    capture._stream = FakeQtStream()
    capture._config = AudioCaptureConfig()
    capture._pcm.extend(b"\x00\x00" * 160)

    with pytest.raises(AudioDeviceError, match="PipeWire or PulseAudio"):
        capture.stop()

    assert not capture.is_recording
    assert not manager.directory.exists()


def test_capture_config_rejects_unsupported_pcm_width() -> None:
    with pytest.raises(ValueError, match="16-bit"):
        AudioCaptureConfig(sample_width_bytes=4)
