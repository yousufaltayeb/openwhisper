"""Validated local compute capability probes.

Device enumeration is not considered a capability. A probe creates a tiny
disposable WAV and completes a local Faster-Whisper inference, so Settings can
distinguish a visible-but-unusable accelerator from a backend that works.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from struct import pack
from typing import Any


class ComputeBackend(StrEnum):
    CPU = "cpu"
    NVIDIA = "nvidia"
    AMD = "amd"


@dataclass(frozen=True, slots=True)
class ComputeCapability:
    id: str
    backend: ComputeBackend
    available: bool
    validated: bool
    supported_compute_types: tuple[str, ...]
    failure_reason: str | None = None

    @property
    def reason(self) -> str | None:
        """Compatibility alias used by older host adapters."""

        return self.failure_reason


@dataclass(frozen=True, slots=True)
class ComputeProbeResult:
    backend: ComputeBackend
    available: bool
    validated: bool
    supported_compute_types: tuple[str, ...]
    failure_reason: str | None = None

    def capability(self) -> ComputeCapability:
        return ComputeCapability(
            id=self.backend.value,
            backend=self.backend,
            available=self.available,
            validated=self.validated,
            supported_compute_types=self.supported_compute_types,
            failure_reason=self.failure_reason,
        )


ModelFactory = Callable[..., Any]


class ComputeProbe:
    """Run complete local probes for CPU, NVIDIA CUDA, and AMD ROCm."""

    _types = {
        ComputeBackend.CPU: ("auto", "int8", "int8_float32"),
        ComputeBackend.NVIDIA: ("auto", "float16", "int8_float16"),
        ComputeBackend.AMD: ("auto", "float16", "int8_float16"),
    }

    def __init__(
        self,
        *,
        model: str = "tiny",
        model_factory: ModelFactory | None = None,
        model_root: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.model = model
        self.model_factory = model_factory or _default_model_factory
        self._isolated_default_probe = model_factory is None
        self.model_root = Path(model_root) if model_root is not None else None
        self.environment = os.environ if environment is None else environment

    def probe(self, backend: ComputeBackend | str) -> ComputeProbeResult:
        try:
            selected = ComputeBackend(backend)
        except ValueError as exc:
            raise ValueError("unsupported compute backend") from exc
        device = "cpu" if selected is ComputeBackend.CPU else "cuda"
        try:
            with _disposable_probe_audio() as audio_path:
                if self._isolated_default_probe:
                    return self._probe_subprocess(selected, device, audio_path)
                model = self._load_model(selected, device)
                transcribe = getattr(model, "transcribe")
                segments, _info = transcribe(str(audio_path), language="en")
                # Faster-Whisper returns a lazy generator. Consume it so the
                # probe covers the actual backend inference call.
                tuple(segments)
        except ImportError:
            return ComputeProbeResult(
                selected,
                False,
                False,
                self._types[selected],
                "Faster-Whisper is not installed.",
            )
        except Exception:
            return ComputeProbeResult(
                selected,
                False,
                False,
                self._types[selected],
                f"{selected.value.title()} inference probe failed.",
            )
        return ComputeProbeResult(selected, True, True, self._types[selected])

    def _probe_subprocess(
        self,
        backend: ComputeBackend,
        device: str,
        audio_path: Path,
    ) -> ComputeProbeResult:
        extension_variable = {
            ComputeBackend.NVIDIA: "OPENWHISPER_NVIDIA_EXTENSION",
            ComputeBackend.AMD: "OPENWHISPER_AMD_EXTENSION",
        }.get(backend)
        extension = self.environment.get(extension_variable, "") if extension_variable else ""
        if extension_variable and (not extension or not Path(extension).is_dir()):
            return ComputeProbeResult(
                backend,
                False,
                False,
                self._types[backend],
                f"{backend.value.title()} runtime extension is not installed.",
            )

        environment = dict(self.environment)
        if extension:
            root = Path(extension).absolute()
            libraries = [path for path in (root / "lib", root / "lib64") if path.is_dir()]
            if libraries:
                current = environment.get("LD_LIBRARY_PATH", "")
                environment["LD_LIBRARY_PATH"] = os.pathsep.join(
                    [*(str(path) for path in libraries), *([current] if current else [])]
                )
            site_packages = sorted((root / "lib").glob("python*/site-packages"))
            if site_packages:
                current = environment.get("PYTHONPATH", "")
                environment["PYTHONPATH"] = os.pathsep.join(
                    [*(str(path) for path in site_packages), *([current] if current else [])]
                )
        environment["PYTHONNOUSERSITE"] = "1"
        compute_type = "int8" if backend is ComputeBackend.CPU else "float16"
        command = [
            sys.executable,
            "-c",
            _ISOLATED_PROBE_SCRIPT,
            self.model,
            device,
            compute_type,
            str(audio_path),
            str(self.model_root) if self.model_root is not None else "",
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ComputeProbeResult(
                backend,
                False,
                False,
                self._types[backend],
                f"{backend.value.title()} inference probe could not complete.",
            )
        if completed.returncode != 0:
            return ComputeProbeResult(
                backend,
                False,
                False,
                self._types[backend],
                f"{backend.value.title()} inference probe failed.",
            )
        try:
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            supported = tuple(
                value for value in payload.get("supported", ()) if isinstance(value, str)
            )
        except (IndexError, json.JSONDecodeError, AttributeError):
            supported = self._types[backend]
        return ComputeProbeResult(backend, True, True, supported or self._types[backend])

    def probe_all(self) -> tuple[ComputeCapability, ...]:
        # The ordering is intentional: automatic compute follows the same
        # NVIDIA -> AMD -> CPU preference exposed in the Settings UI.
        return tuple(
            self.probe(backend).capability()
            for backend in (ComputeBackend.NVIDIA, ComputeBackend.AMD, ComputeBackend.CPU)
        )

    @staticmethod
    def choose(
        selection: str,
        capabilities: tuple[ComputeCapability, ...],
    ) -> ComputeBackend:
        try:
            selected = str(selection)
            if selected == "auto":
                for candidate in (ComputeBackend.NVIDIA, ComputeBackend.AMD, ComputeBackend.CPU):
                    if any(
                        item.backend is candidate and item.available and item.validated
                        for item in capabilities
                    ):
                        return candidate
                # CPU is the safe final choice even when its probe is not
                # available; the caller will surface the provider error.
                return ComputeBackend.CPU
            return ComputeBackend(selected)
        except ValueError as exc:
            raise ValueError("unsupported compute selection") from exc

    def _load_model(self, backend: ComputeBackend, device: str) -> Any:
        compute_type = "int8" if backend is ComputeBackend.CPU else "float16"
        kwargs = {
            "download_root": str(self.model_root) if self.model_root is not None else None,
        }
        try:
            return self.model_factory(self.model, device, compute_type, kwargs)
        except TypeError:
            try:
                return self.model_factory(self.model, device, compute_type)
            except TypeError:
                return self.model_factory(self.model, device)


def _default_model_factory(
    model: str,
    device: str,
    compute_type: str,
    options: Mapping[str, object] | None = None,
) -> Any:
    from faster_whisper import WhisperModel

    kwargs = {key: value for key, value in (options or {}).items() if value is not None}
    kwargs["local_files_only"] = True
    return WhisperModel(model, device=device, compute_type=compute_type, **kwargs)


_ISOLATED_PROBE_SCRIPT = """
import json
import sys

import ctranslate2
from faster_whisper import WhisperModel

model, device, compute_type, audio_path, model_root = sys.argv[1:]
options = {"local_files_only": True}
if model_root:
    options["download_root"] = model_root
whisper = WhisperModel(model, device=device, compute_type=compute_type, **options)
segments, _info = whisper.transcribe(audio_path, language="en")
tuple(segments)
print(json.dumps({"supported": ctranslate2.get_supported_compute_types(device)}))
"""


class _disposable_probe_audio:
    """Context manager for a short valid PCM WAV in a private temp dir."""

    def __enter__(self) -> Path:
        self._directory = tempfile.TemporaryDirectory(prefix="openwhisper-probe-")
        path = Path(self._directory.name) / "probe.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            # Silence is deterministic and contains no user audio.
            output.writeframes(pack("<h", 0) * 3_200)
        self._path = path
        return path

    def __exit__(self, *_args: object) -> None:
        self._directory.cleanup()
