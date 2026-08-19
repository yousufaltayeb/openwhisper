"""Local transcription adapters with lazy optional model loading."""

from __future__ import annotations

import threading
import wave
from array import array
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from math import sqrt
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ._shared import emit_progress, ensure_not_cancelled, validate_request
from .connection import connection_result
from .contracts import (
    ConnectionTestResult,
    ProviderCapabilities,
    ProviderProgressStage,
    TranscriptionRequest,
    TranscriptResult,
    TranscriptSegment,
)
from .errors import ProviderError, ProviderErrorKind
from .models import COHERE_LOCAL_ARABIC_MODEL, FASTER_WHISPER_DEFAULT_MODEL
from .normalization import optional_number, transcript_result, wav_duration_seconds
from .streaming import StablePrefixReconciler

FasterWhisperModelFactory = Callable[[str, str, str], Any]
CoherePipelineFactory = Callable[[str, str], Callable[..., object]]


class FasterWhisperProvider:
    """Local Faster-Whisper adapter with batch and chunk-streaming contracts.

    The model is loaded only on the first request.  Construction is therefore
    safe during application startup and does not download a model or allocate
    a GPU simply because the provider appears in a settings list.
    """

    name = "faster-whisper"
    capabilities = ProviderCapabilities(
        batch=True,
        streaming=True,
        languages=None,
        timestamps=True,
    )

    def __init__(
        self,
        *,
        model: str = FASTER_WHISPER_DEFAULT_MODEL,
        device: str = "auto",
        compute_type: str = "auto",
        model_factory: FasterWhisperModelFactory | None = None,
    ) -> None:
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self._model_factory = model_factory or _default_faster_whisper_model
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    def transcribe(self, request: TranscriptionRequest) -> TranscriptResult:
        validate_request(self.name, self.capabilities, request)
        try:
            emit_progress(self.name, request.progress, ProviderProgressStage.LOADING_MODEL)
            ensure_not_cancelled(self.name, request.cancellation)
            model = self._get_model()
            ensure_not_cancelled(self.name, request.cancellation)
            emit_progress(self.name, request.progress, ProviderProgressStage.TRANSCRIBING)
            raw_segments, info = model.transcribe(
                str(request.audio_path),
                language=_language_hint(request.language),
                initial_prompt=_recognition_prompt(request),
                word_timestamps=request.timestamps,
            )
            segments_list: list[TranscriptSegment] = []
            for segment in raw_segments:
                ensure_not_cancelled(self.name, request.cancellation)
                segments_list.append(_faster_whisper_segment(segment))
            segments = tuple(segments_list)
        except ProviderError:
            raise
        except ValueError as exc:
            raise ProviderError(
                self.name,
                ProviderErrorKind.INVALID_AUDIO,
                "faster-whisper rejected the audio request",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                self.name,
                ProviderErrorKind.UNAVAILABLE,
                "faster-whisper could not complete transcription",
            ) from exc

        text = " ".join(segment.text for segment in segments).strip()
        language = _read_field(info, "language") or _language_hint(request.language)
        duration = _read_field(info, "duration")
        result = transcript_result(
            provider=self.name,
            model=self.model,
            text=text,
            language=language,
            duration_seconds=(
                duration if duration is not None else wav_duration_seconds(request.audio_path)
            ),
            segments=segments if request.timestamps else (),
        )
        emit_progress(self.name, request.progress, ProviderProgressStage.COMPLETED, fraction=1)
        return result

    def transcribe_stream(
        self, requests: Iterable[TranscriptionRequest]
    ) -> Iterator[TranscriptResult]:
        """Transcribe successive locally captured chunks without cloud I/O.

        The application's live-insertion controller supplies finalized audio
        chunks.  Keeping this method request-based avoids coupling a local
        model adapter to a microphone or GUI event loop, while exposing the
        capability promised by the v0.1 local provider.
        """

        reconciler = StablePrefixReconciler()
        for request in requests:
            ensure_not_cancelled(self.name, request.cancellation)
            result = self.transcribe(request)
            reconciled = reconciler.reconcile_chunk(result.text)
            yield TranscriptResult(
                text=reconciled.insertion,
                language=result.language,
                provider=result.provider,
                model=result.model,
                duration_seconds=result.duration_seconds,
                # Chunk boundaries can invalidate timestamps after overlap
                # removal; consumers performing live insertion only need text.
                segments=result.segments if reconciled.insertion == result.text else (),
            )

    def test_connection(self) -> ConnectionTestResult:
        return connection_result(self.name, self.model, self._get_model)

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                self._model = self._model_factory(self.model, self.device, self.compute_type)
            except ImportError as exc:
                raise ProviderError(
                    self.name,
                    ProviderErrorKind.CONFIGURATION,
                    "Faster-Whisper is not installed",
                ) from exc
            except Exception as exc:
                raise ProviderError(
                    self.name,
                    ProviderErrorKind.UNAVAILABLE,
                    "Faster-Whisper model could not be loaded",
                ) from exc
        return self._model


class CohereArabicLocalProvider:
    """Optional local Hugging Face pipeline for Cohere Transcribe Arabic."""

    name = "cohere-local"
    capabilities = ProviderCapabilities(
        batch=True,
        streaming=False,
        languages=frozenset({"ar", "en"}),
        timestamps=False,
        required_configuration=("cohere-local optional dependencies",),
    )

    def __init__(
        self,
        *,
        model: str = COHERE_LOCAL_ARABIC_MODEL,
        device: str = "auto",
        pipeline_factory: CoherePipelineFactory | None = None,
        use_vad: bool = True,
        vad_threshold: int = 250,
    ) -> None:
        if vad_threshold < 0:
            raise ValueError("vad_threshold cannot be negative")
        self.model = model
        self.device = device
        self.use_vad = use_vad
        self.vad_threshold = vad_threshold
        self._pipeline_factory = pipeline_factory or _default_cohere_pipeline
        self._pipeline: Callable[..., object] | None = None
        self._pipeline_lock = threading.Lock()

    def transcribe(self, request: TranscriptionRequest) -> TranscriptResult:
        if request.language is None or request.language.strip().casefold() == "auto":
            raise ProviderError(
                self.name,
                ProviderErrorKind.UNSUPPORTED_CAPABILITY,
                "cohere-local requires an explicit Arabic or English language selection",
            )
        validate_request(self.name, self.capabilities, request)
        try:
            emit_progress(self.name, request.progress, ProviderProgressStage.LOADING_MODEL)
            ensure_not_cancelled(self.name, request.cancellation)
            emit_progress(self.name, request.progress, ProviderProgressStage.TRANSCRIBING)
            with _vad_audio_path(
                request.audio_path,
                enabled=self.use_vad,
                threshold=self.vad_threshold,
            ) as audio_path:
                response = self._get_pipeline()(str(audio_path))
            ensure_not_cancelled(self.name, request.cancellation)
        except ProviderError:
            raise
        except ValueError as exc:
            raise ProviderError(
                self.name,
                ProviderErrorKind.INVALID_AUDIO,
                "cohere-local rejected the audio request",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                self.name,
                ProviderErrorKind.UNAVAILABLE,
                "cohere-local could not complete transcription",
            ) from exc
        text = response.get("text") if isinstance(response, dict) else None
        result = transcript_result(
            provider=self.name,
            model=self.model,
            text=text,
            language=_language_hint(request.language) or "ar",
            duration_seconds=wav_duration_seconds(request.audio_path),
        )
        emit_progress(self.name, request.progress, ProviderProgressStage.COMPLETED, fraction=1)
        return result

    def test_connection(self) -> ConnectionTestResult:
        return connection_result(self.name, self.model, self._get_pipeline)

    def _get_pipeline(self) -> Callable[..., object]:
        if self._pipeline is not None:
            return self._pipeline
        with self._pipeline_lock:
            if self._pipeline is not None:
                return self._pipeline
            try:
                self._pipeline = self._pipeline_factory(self.model, self.device)
            except ImportError as exc:
                raise ProviderError(
                    self.name,
                    ProviderErrorKind.CONFIGURATION,
                    "Cohere local transcription dependencies are not installed",
                ) from exc
            except Exception as exc:
                raise ProviderError(
                    self.name,
                    ProviderErrorKind.UNAVAILABLE,
                    "Cohere local model could not be loaded",
                ) from exc
        return self._pipeline


def _default_faster_whisper_model(model: str, device: str, compute_type: str) -> Any:
    # Public settings use vendor names; CTranslate2's HIP and CUDA builds
    # expose the same ``cuda`` device token. The selected Flatpak extension is
    # added by ``_load_accelerator_extension`` before this import.
    _load_accelerator_extension(device)
    from faster_whisper import WhisperModel

    runtime_device = {"nvidia": "cuda", "amd": "cuda"}.get(device, device)
    return WhisperModel(model, device=runtime_device, compute_type=compute_type)


def _load_accelerator_extension(device: str) -> None:
    """Prepend the matching optional CTranslate2 extension before import."""

    import os
    import sys

    if device == "nvidia":
        root = os.environ.get("OPENWHISPER_NVIDIA_EXTENSION")
    elif device == "amd":
        root = os.environ.get("OPENWHISPER_AMD_EXTENSION")
    else:
        root = None
    if not root:
        return
    root = os.path.abspath(root)
    lib = os.path.join(root, "lib")
    site_packages = [
        os.path.join(root, "lib", entry, "site-packages")
        for entry in os.listdir(os.path.join(root, "lib"))
        if entry.startswith("python")
    ] if os.path.isdir(os.path.join(root, "lib")) else []
    for candidate in [lib, *site_packages]:
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)


def _default_cohere_pipeline(model: str, device: str) -> Callable[..., object]:
    from transformers import pipeline

    options: dict[str, object] = {"task": "automatic-speech-recognition", "model": model}
    if device and device != "auto":
        options["device"] = device
    return pipeline(**options)


def _faster_whisper_segment(value: object) -> TranscriptSegment:
    text = _read_field(value, "text")
    start = optional_number(_read_field(value, "start"))
    end = optional_number(_read_field(value, "end"))
    confidence = optional_number(_read_field(value, "avg_logprob"))
    if not isinstance(text, str) or start is None or end is None:
        raise ProviderError(
            "faster-whisper",
            ProviderErrorKind.MALFORMED_RESPONSE,
            "faster-whisper returned an invalid transcript segment",
        )
    try:
        return TranscriptSegment(text, start, end, confidence)
    except ValueError as exc:
        raise ProviderError(
            "faster-whisper",
            ProviderErrorKind.MALFORMED_RESPONSE,
            "faster-whisper returned an invalid transcript segment",
        ) from exc


def _read_field(value: object, field: str) -> object:
    return value.get(field) if isinstance(value, dict) else getattr(value, field, None)


def _language_hint(value: str | None) -> str | None:
    if value is None or value.strip().casefold() == "auto":
        return None
    return value.strip()


def _recognition_prompt(request: TranscriptionRequest) -> str | None:
    parts = [request.prompt.strip()] if request.prompt and request.prompt.strip() else []
    if request.recognition_hints:
        parts.append("Vocabulary: " + ", ".join(request.recognition_hints))
    return "\n".join(parts) or None


@contextmanager
def _vad_audio_path(path: Path, *, enabled: bool, threshold: int):
    """Trim leading/trailing low-energy PCM while always deleting its scratch WAV."""

    trimmed = _trim_wave_silence(path, threshold=threshold) if enabled else None
    try:
        yield trimmed or path
    finally:
        if trimmed is not None:
            trimmed.unlink(missing_ok=True)


def _trim_wave_silence(path: Path, *, threshold: int, frame_ms: int = 30) -> Path | None:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.readframes(source.getnframes())
    except (OSError, wave.Error):
        return None
    if sample_width != 2 or channels <= 0 or sample_rate <= 0 or not frames:
        return None

    samples = array("h")
    samples.frombytes(frames)
    samples_per_window = max(channels, sample_rate * channels * frame_ms // 1000)
    active: list[int] = []
    for offset in range(0, len(samples), samples_per_window):
        window = samples[offset : offset + samples_per_window]
        if not window:
            continue
        rms = sqrt(sum(sample * sample for sample in window) / len(window))
        if rms >= threshold:
            active.append(offset)
    if not active:
        return None
    padding = sample_rate * channels // 5
    start = max(0, active[0] - padding)
    end = min(len(samples), active[-1] + samples_per_window + padding)
    if start == 0 and end == len(samples):
        return None

    with NamedTemporaryFile(
        prefix="openwhisper-vad-", suffix=".wav", dir=path.parent, delete=False
    ) as temporary:
        trimmed_path = Path(temporary.name)
    try:
        with wave.open(str(trimmed_path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(sample_width)
            output.setframerate(sample_rate)
            output.writeframes(samples[start:end].tobytes())
    except Exception:
        trimmed_path.unlink(missing_ok=True)
        raise
    return trimmed_path
