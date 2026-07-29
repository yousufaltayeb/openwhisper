"""Audio capture boundaries and temporary-audio lifecycle management."""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CapturedAudio:
    path: Path
    duration_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class AudioDevice:
    """A microphone exposed by the active desktop audio stack.

    ``id`` is an opaque stable identifier suitable for storing in a mode or
    preference.  It is deliberately not a PulseAudio source name, so the UI
    does not leak backend-specific capture details into its public model.
    """

    id: str
    description: str
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class AudioCaptureConfig:
    """Capture settings shared by desktop audio implementations."""

    device_id: str | None = None
    sample_rate: int = 16_000
    channels: int = 1
    sample_width_bytes: int = 2
    trim_silence: bool = True
    silence_threshold: int = 160

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.channels <= 0 or self.sample_width_bytes <= 0:
            raise ValueError("audio format values must be positive")
        if self.sample_width_bytes != 2:
            raise ValueError("only 16-bit PCM capture is supported")
        if self.silence_threshold < 0 or self.silence_threshold > 32_767:
            raise ValueError("silence_threshold must be between 0 and 32767")


@dataclass(frozen=True, slots=True)
class AudioLevelEvent:
    """A normalized microphone-level measurement for a UI meter."""

    rms: float
    peak: float
    elapsed_seconds: float


class AudioDeviceError(RuntimeError):
    """Actionable microphone/device failure safe to show in the desktop UI."""


AudioLevelListener = Callable[[AudioLevelEvent], None]


@runtime_checkable
class AudioCapture(Protocol):
    def available_devices(self) -> Sequence[AudioDevice]:
        """Return selectable input devices without opening one."""

    def start(self, config: AudioCaptureConfig | None = None) -> None:
        """Begin a new recording."""

    def stop(self) -> CapturedAudio:
        """Stop and return the completed temporary recording."""

    def cancel(self) -> None:
        """Stop and discard the active recording."""

    def read_pcm(self) -> bytes | None:
        """Return the next captured PCM fragment for live consumers, if any."""


class TempAudioManager:
    """Own temporary recordings and ensure only its own files are removed."""

    prefix = "openwhisper-"
    suffix = ".wav"

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def create_path(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=self.prefix,
            suffix=self.suffix,
            dir=self.directory,
        )
        # The recorder opens this path itself. Closing is sufficient; unlike a
        # NamedTemporaryFile this also works for subprocess recorders.
        os.close(descriptor)
        return Path(raw_path).resolve()

    def delete(self, path: Path) -> None:
        candidate = Path(path)
        # Do not let a path such as ``audio/../somewhere/file.wav`` escape the
        # cache directory. Requiring the direct parent also means this manager
        # never recursively cleans an unexpected subdirectory.
        if candidate.parent.resolve() != self.directory.resolve():
            raise ValueError("refusing to delete audio outside the temp directory")
        if not candidate.name.startswith(self.prefix) or candidate.suffix != self.suffix:
            raise ValueError("refusing to delete an unmanaged audio file")
        candidate.unlink(missing_ok=True)

    def cleanup_stale(
        self,
        *,
        max_age: timedelta = timedelta(0),
        now: float | None = None,
    ) -> int:
        """Delete OpenWhisper leftovers, normally once during application startup."""

        if not self.directory.exists():
            return 0
        cutoff = (time.time() if now is None else now) - max_age.total_seconds()
        removed = 0
        for path in self.directory.glob(f"{self.prefix}*{self.suffix}"):
            try:
                if path.stat().st_mtime <= cutoff:
                    self.delete(path)
                    removed += 1
            except (FileNotFoundError, OSError):
                # Stale cleanup is best effort. A recorder or another process
                # may remove a file between globbing and stat/unlink.
                continue
        return removed


ProcessFactory = Callable[..., subprocess.Popen[bytes]]
CommandFactory = Callable[[Path], Sequence[str]]


def _default_parec_command(path: Path) -> Sequence[str]:
    return (
        "parec",
        "--file-format=wav",
        "--format=s16le",
        "--channels=1",
        "--rate=16000",
        str(path),
    )


def _default_buffered_parec_command() -> Sequence[str]:
    """Capture raw signed 16-bit PCM to stdout for incremental WAV chunks."""

    return (
        "parec",
        "--file-format=raw",
        "--format=s16le",
        "--channels=1",
        "--rate=16000",
    )


class ParecAudioCapture:
    """Legacy PulseAudio/PipeWire adapter retained for source development.

    Packaged builds use :class:`QtMultimediaAudioCapture`.  Keeping this
    narrow adapter avoids breaking existing developer scripts while making its
    inability to enumerate or select devices explicit.
    """

    def __init__(
        self,
        temporary_audio: TempAudioManager,
        *,
        process_factory: ProcessFactory = subprocess.Popen,
        command_factory: CommandFactory = _default_parec_command,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._temporary_audio = temporary_audio
        self._process_factory = process_factory
        self._command_factory = command_factory
        self._monotonic = monotonic
        self._process: subprocess.Popen[bytes] | None = None
        self._path: Path | None = None
        self._started_at: float | None = None

    def available_devices(self) -> Sequence[AudioDevice]:
        return ()

    def start(self, config: AudioCaptureConfig | None = None) -> None:
        if self._process is not None:
            raise RuntimeError("audio capture is already running")
        if config is not None and config.device_id is not None:
            raise AudioDeviceError(
                "This development capture backend cannot select a microphone; "
                "run the Qt desktop application instead."
            )
        path = self._temporary_audio.create_path()
        try:
            process = self._process_factory(
                list(self._command_factory(path)),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            started_at = self._monotonic()
        except Exception:
            self._temporary_audio.delete(path)
            raise
        self._path = path
        self._started_at = started_at
        self._process = process

    def stop(self) -> CapturedAudio:
        process, path, started_at = self._active()
        try:
            self._stop_process(process)
            duration = max(0.0, self._monotonic() - started_at)
        except Exception:
            # There is no usable recording if the recorder cannot be stopped.
            # Delete it here because callers do not receive a CapturedAudio to
            # clean in their own finally block.
            self._temporary_audio.delete(path)
            raise
        finally:
            self._reset()
        return CapturedAudio(path=path, duration_seconds=duration)

    def cancel(self) -> None:
        if self._process is None:
            return
        process, path, _started_at = self._active()
        try:
            self._stop_process(process)
        finally:
            self._reset()
            self._temporary_audio.delete(path)

    def read_pcm(self) -> bytes | None:
        # This recorder writes directly to a WAV file and has no safe live
        # stream.  BufferedParecAudioCapture remains available to developers
        # who specifically need the old command-line live path.
        return None

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        """End a recorder without leaving a child behind after a timeout."""

        # ``poll`` is intentionally optional for small injectable fakes used
        # by consumers' tests. A real Popen always provides it.
        poll = getattr(process, "poll", None)
        if poll is None or poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _active(self) -> tuple[subprocess.Popen[bytes], Path, float]:
        if self._process is None or self._path is None or self._started_at is None:
            raise RuntimeError("audio capture is not running")
        return self._process, self._path, self._started_at

    def _reset(self) -> None:
        self._process = None
        self._path = None
        self._started_at = None


class BufferedParecAudioCapture:
    """Capture PCM from parec and publish self-contained, valid WAV snapshots.

    :meth:`take_chunk` returns only audio not returned by an earlier call, so a
    live provider can transcribe non-overlapping finalized chunks. :meth:`stop`
    still follows :class:`AudioCapture` semantics and returns the complete WAV;
    :meth:`stop_with_final_chunk` additionally returns the final unclaimed tail
    for a live worker. Every returned path is owned by ``temporary_audio`` and
    must be deleted by the consumer after transcription.
    """

    def __init__(
        self,
        temporary_audio: TempAudioManager,
        *,
        process_factory: ProcessFactory = subprocess.Popen,
        command_factory: Callable[[], Sequence[str]] = _default_buffered_parec_command,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width_bytes: int = 2,
        read_size: int = 3200,
    ) -> None:
        if sample_rate <= 0 or channels <= 0 or sample_width_bytes <= 0:
            raise ValueError("audio format values must be positive")
        if read_size <= 0:
            raise ValueError("read_size must be positive")
        self._temporary_audio = temporary_audio
        self._process_factory = process_factory
        self._command_factory = command_factory
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width_bytes = sample_width_bytes
        self.read_size = read_size
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._buffer = bytearray()
        self._chunk_offset = 0
        self._reader_error: Exception | None = None
        self._lock = threading.RLock()

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._process is not None

    def available_devices(self) -> Sequence[AudioDevice]:
        return ()

    def start(self, config: AudioCaptureConfig | None = None) -> None:
        with self._lock:
            if self._process is not None:
                raise RuntimeError("audio capture is already running")
            if config is not None and config.device_id is not None:
                raise AudioDeviceError(
                    "This development capture backend cannot select a microphone; "
                    "run the Qt desktop application instead."
                )
            process = self._process_factory(
                list(self._command_factory()),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stream = getattr(process, "stdout", None)
            if stream is None:
                try:
                    ParecAudioCapture._stop_process(process)
                finally:
                    raise RuntimeError("parec did not provide an audio stream")
            self._process = process
            self._buffer.clear()
            self._chunk_offset = 0
            self._reader_error = None
            self._reader = threading.Thread(
                target=self._read_pcm,
                args=(stream,),
                name="openwhisper-audio-buffer",
                daemon=True,
            )
            self._reader.start()

    def take_chunk(self, *, minimum_duration_seconds: float = 0.0) -> CapturedAudio | None:
        """Write and consume the next non-overlapping WAV chunk, if long enough."""

        if minimum_duration_seconds < 0:
            raise ValueError("minimum_duration_seconds cannot be negative")
        with self._lock:
            self._ensure_active()
            end = self._aligned_length(len(self._buffer))
            data = bytes(self._buffer[self._chunk_offset : end])
            if not data or self._duration(data) < minimum_duration_seconds:
                return None
            self._chunk_offset = end
        return self._write_wav(data)

    def snapshot(self) -> CapturedAudio | None:
        """Write a complete valid WAV snapshot without consuming live audio."""

        with self._lock:
            self._ensure_active()
            data = bytes(self._buffer[: self._aligned_length(len(self._buffer))])
        return self._write_wav(data) if data else None

    def stop(self) -> CapturedAudio:
        complete, _final_chunk = self._stop(include_final_chunk=False)
        return complete

    def stop_with_final_chunk(self) -> tuple[CapturedAudio, CapturedAudio | None]:
        """Stop capture and return both complete audio and the live stream tail."""

        return self._stop(include_final_chunk=True)

    def cancel(self) -> None:
        with self._lock:
            self._ensure_active()
            process = self._process
        try:
            ParecAudioCapture._stop_process(process)
        finally:
            try:
                self._join_reader()
            finally:
                self._reset()

    def read_pcm(self) -> bytes | None:
        """Return a non-overlapping raw PCM fragment when the reader has data."""

        with self._lock:
            self._ensure_active()
            end = self._aligned_length(len(self._buffer))
            data = bytes(self._buffer[self._chunk_offset : end])
            self._chunk_offset = end
        return data or None

    def _stop(self, *, include_final_chunk: bool) -> tuple[CapturedAudio, CapturedAudio | None]:
        with self._lock:
            self._ensure_active()
            process = self._process
        stop_error: Exception | None = None
        try:
            ParecAudioCapture._stop_process(process)
        except Exception as error:
            stop_error = error
        try:
            self._join_reader()
        except Exception as error:
            if stop_error is None:
                stop_error = error

        with self._lock:
            error = self._reader_error
            end = self._aligned_length(len(self._buffer))
            complete_data = bytes(self._buffer[:end])
            final_data = bytes(self._buffer[self._chunk_offset : end])
            self._reset()
        if stop_error is not None:
            raise stop_error
        if error is not None:
            raise RuntimeError("audio capture stream failed") from error

        complete = self._write_wav(complete_data)
        final_chunk = self._write_wav(final_data) if include_final_chunk and final_data else None
        return complete, final_chunk

    def _read_pcm(self, stream: object) -> None:
        try:
            read = getattr(stream, "read")
            while True:
                data = read(self.read_size)
                if not data:
                    return
                if not isinstance(data, bytes):
                    raise TypeError("parec audio stream returned non-bytes data")
                with self._lock:
                    self._buffer.extend(data)
        except Exception as error:
            with self._lock:
                self._reader_error = error

    def _join_reader(self) -> None:
        reader = self._reader
        if reader is not None:
            reader.join(timeout=5)
            if reader.is_alive():
                raise RuntimeError("audio capture stream did not stop")

    def _write_wav(self, pcm: bytes) -> CapturedAudio:
        path = self._temporary_audio.create_path()
        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(self.channels)
                output.setsampwidth(self.sample_width_bytes)
                output.setframerate(self.sample_rate)
                output.writeframes(pcm)
        except Exception:
            self._temporary_audio.delete(path)
            raise
        return CapturedAudio(path=path, duration_seconds=self._duration(pcm))

    @property
    def _frame_size(self) -> int:
        return self.channels * self.sample_width_bytes

    def _aligned_length(self, length: int) -> int:
        return length - length % self._frame_size

    def _duration(self, pcm: bytes) -> float:
        return len(pcm) / (self._frame_size * self.sample_rate)

    def _ensure_active(self) -> None:
        if self._process is None:
            raise RuntimeError("audio capture is not running")

    def _reset(self) -> None:
        self._process = None
        self._reader = None
        self._buffer.clear()
        self._chunk_offset = 0


class QtMultimediaAudioCapture:
    """Qt Multimedia microphone capture for the sandboxed desktop application.

    Qt talks to PipeWire/PulseAudio through the runtime rather than spawning a
    host command.  PCM is held only for the active recording, exposed in small
    fragments to live consumers, and written to an application-owned WAV when
    recording ends.  A cancelled capture never creates a retained recording.

    Imports are deliberately lazy: core tests and command-line tooling can use
    the rest of OpenWhisper without requiring a working GUI/audio server.
    """

    def __init__(
        self,
        temporary_audio: TempAudioManager,
        *,
        level_listener: AudioLevelListener | None = None,
        default_config: AudioCaptureConfig | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._temporary_audio = temporary_audio
        self._level_listener = level_listener
        self._default_config = default_config or AudioCaptureConfig()
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._source: object | None = None
        self._stream: object | None = None
        self._config: AudioCaptureConfig | None = None
        self._pcm = bytearray()
        self._stream_offset = 0
        self._started_at: float | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._source is not None

    def available_devices(self) -> Sequence[AudioDevice]:
        """Enumerate the microphones known to Qt Multimedia."""

        media_devices = self._media_devices()
        default = media_devices.defaultAudioInput()
        default_id = self._device_id(default)
        devices: list[AudioDevice] = []
        for device in media_devices.audioInputs():
            identifier = self._device_id(device)
            description = str(device.description()).strip() or "Microphone"
            devices.append(
                AudioDevice(
                    id=identifier,
                    description=description,
                    is_default=identifier == default_id,
                )
            )
        return tuple(devices)

    def start(self, config: AudioCaptureConfig | None = None) -> None:
        config = config or self._default_config
        with self._lock:
            if self._source is not None:
                raise RuntimeError("audio capture is already running")
            device = self._select_device(config.device_id)
            source, stream = self._start_source(device, config)
            self._source = source
            self._stream = stream
            self._config = config
            self._pcm.clear()
            self._stream_offset = 0
            self._started_at = self._monotonic()

    def stop(self) -> CapturedAudio:
        with self._lock:
            source, config = self._active()
        try:
            # Qt emits the final readyRead synchronously on most backends; an
            # explicit drain also handles backends that do not.
            getattr(source, "stop")()
            self._consume_available()
            with self._lock:
                pcm = bytes(self._pcm)
                self._reset()
            if config.trim_silence:
                pcm = self._trim_silence(pcm, config)
            return self._write_wav(pcm, config)
        except Exception:
            with self._lock:
                self._reset()
            raise

    def cancel(self) -> None:
        with self._lock:
            source = self._source
            if source is None:
                return
        try:
            getattr(source, "stop")()
        finally:
            with self._lock:
                self._reset()

    def read_pcm(self) -> bytes | None:
        """Consume PCM received since the prior call for live transcription."""

        self._consume_available()
        with self._lock:
            end = self._aligned_length(len(self._pcm), self._config)
            data = bytes(self._pcm[self._stream_offset : end])
            self._stream_offset = end
        return data or None

    def take_chunk(self, *, minimum_duration_seconds: float = 0.0) -> CapturedAudio | None:
        """Materialize the next non-overlapping live PCM slice as a WAV file."""

        if minimum_duration_seconds < 0:
            raise ValueError("minimum_duration_seconds cannot be negative")
        self._consume_available()
        with self._lock:
            config = self._config
            if config is None or self._source is None:
                raise RuntimeError("audio capture is not running")
            end = self._aligned_length(len(self._pcm), config)
            pcm = bytes(self._pcm[self._stream_offset : end])
            duration = len(pcm) / (config.sample_rate * config.channels * config.sample_width_bytes)
            if not pcm or duration < minimum_duration_seconds:
                return None
            self._stream_offset = end
        # Do not trim live chunks: a leading quiet frame provides harmless
        # timing continuity and avoids joining words across chunk boundaries.
        return self._write_wav(pcm, config)

    def _media_devices(self) -> object:
        try:
            from PySide6.QtMultimedia import QMediaDevices
        except ImportError as exc:
            raise AudioDeviceError(
                "Qt Multimedia is unavailable. Reinstall the OpenWhisper Flatpak or PySide6."
            ) from exc
        return QMediaDevices()

    def _select_device(self, requested_id: str | None) -> object:
        media_devices = self._media_devices()
        devices = tuple(media_devices.audioInputs())
        if not devices:
            raise AudioDeviceError(
                "No microphone is available. Connect or enable one, then reopen OpenWhisper."
            )
        if requested_id is None:
            default = media_devices.defaultAudioInput()
            if self._device_id(default):
                return default
            return devices[0]
        for device in devices:
            if self._device_id(device) == requested_id:
                return device
        raise AudioDeviceError(
            "The selected microphone is no longer available. Choose another microphone in Settings."
        )

    def _start_source(self, device: object, config: AudioCaptureConfig) -> tuple[object, object]:
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSource

            audio_format = QAudioFormat()
            audio_format.setSampleRate(config.sample_rate)
            audio_format.setChannelCount(config.channels)
            audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if not device.isFormatSupported(audio_format):
                raise AudioDeviceError(
                    "The selected microphone cannot provide 16 kHz mono audio. "
                    "Choose another microphone in Settings."
                )
            source = QAudioSource(device, audio_format)
            stream = source.start()
            if stream is None:
                raise AudioDeviceError(
                    "OpenWhisper could not open the microphone. Check the Flatpak microphone "
                    "permission and that no other app has exclusive access."
                )
            stream.readyRead.connect(self._consume_available)
            return source, stream
        except AudioDeviceError:
            raise
        except Exception as exc:
            raise AudioDeviceError(
                "OpenWhisper could not start the microphone. Check the selected device and "
                "the Flatpak microphone permission."
            ) from exc

    def _consume_available(self) -> None:
        with self._lock:
            stream = self._stream
            config = self._config
            started_at = self._started_at
        if stream is None or config is None:
            return
        try:
            available = getattr(stream, "bytesAvailable")()
            if available <= 0:
                return
            raw = getattr(stream, "readAll")()
            data = bytes(raw)
        except Exception:
            # A device disappearing is surfaced when the user stops recording;
            # never make a Qt signal handler raise into the event loop.
            return
        if not data:
            return
        frame_size = config.channels * config.sample_width_bytes
        data = data[: len(data) - len(data) % frame_size]
        if not data:
            return
        with self._lock:
            if stream is not self._stream:
                return
            self._pcm.extend(data)
        if self._level_listener is not None:
            self._emit_level(data, started_at)

    def _emit_level(self, pcm: bytes, started_at: float | None) -> None:
        try:
            values = tuple(
                int.from_bytes(pcm[index : index + 2], "little", signed=True)
                for index in range(0, len(pcm), 2)
            )
            if not values:
                return
            peak = max(abs(value) for value in values) / 32_767
            rms = math.sqrt(sum(value * value for value in values) / len(values)) / 32_767
            self._level_listener(
                AudioLevelEvent(
                    rms=min(1.0, rms),
                    peak=min(1.0, peak),
                    elapsed_seconds=max(0.0, self._monotonic() - (started_at or self._monotonic())),
                )
            )
        except Exception:
            # Meter listeners are presentation code and cannot interrupt audio
            # capture or create a privacy-relevant recording failure.
            return

    def _write_wav(self, pcm: bytes, config: AudioCaptureConfig) -> CapturedAudio:
        path = self._temporary_audio.create_path()
        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(config.channels)
                output.setsampwidth(config.sample_width_bytes)
                output.setframerate(config.sample_rate)
                output.writeframes(pcm)
        except Exception:
            self._temporary_audio.delete(path)
            raise
        duration = len(pcm) / (config.sample_rate * config.channels * config.sample_width_bytes)
        return CapturedAudio(path=path, duration_seconds=duration)

    @staticmethod
    def _device_id(device: object) -> str:
        try:
            raw = bytes(device.id())
        except Exception:
            return ""
        return raw.hex()

    @staticmethod
    def _aligned_length(length: int, config: AudioCaptureConfig | None) -> int:
        if config is None:
            return 0
        frame_size = config.channels * config.sample_width_bytes
        return length - length % frame_size

    @staticmethod
    def _trim_silence(pcm: bytes, config: AudioCaptureConfig) -> bytes:
        """Trim only fully quiet leading/trailing frames; preserve speech PCM."""

        frame_size = config.channels * config.sample_width_bytes
        end = len(pcm) - len(pcm) % frame_size
        if end == 0:
            return b""
        start_frame = 0
        end_frame = end // frame_size

        def quiet(frame_index: int) -> bool:
            offset = frame_index * frame_size
            return all(
                abs(
                    int.from_bytes(
                        pcm[offset + channel * 2 : offset + channel * 2 + 2],
                        "little",
                        signed=True,
                    )
                )
                <= config.silence_threshold
                for channel in range(config.channels)
            )

        while start_frame < end_frame and quiet(start_frame):
            start_frame += 1
        while end_frame > start_frame and quiet(end_frame - 1):
            end_frame -= 1
        return pcm[start_frame * frame_size : end_frame * frame_size]

    def _active(self) -> tuple[object, AudioCaptureConfig]:
        if self._source is None or self._config is None:
            raise RuntimeError("audio capture is not running")
        return self._source, self._config

    def _reset(self) -> None:
        self._source = None
        self._stream = None
        self._config = None
        self._pcm.clear()
        self._stream_offset = 0
        self._started_at = None
