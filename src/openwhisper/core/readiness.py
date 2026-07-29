"""First-run diagnostics that do not capture audio, context, or credentials."""

from __future__ import annotations

import importlib.util
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .audio import AudioCapture
from .desktop import DesktopCapabilities


class ReadinessStatus(StrEnum):
    READY = "ready"
    ACTION_REQUIRED = "action-required"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    id: str
    status: ReadinessStatus
    title: str
    message: str


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return all(check.status is ReadinessStatus.READY for check in self.checks)

    def by_id(self, identifier: str) -> ReadinessCheck:
        return next(check for check in self.checks if check.id == identifier)


ProviderCheck = Callable[[], tuple[bool, str]]


class ReadinessChecker:
    """Assess prerequisites without recording sound or transmitting a request.

    A provider check is intentionally supplied by the explicit "Test
    connection" action.  Startup diagnostics never exercise a BYOK credential
    or make a network request on their own.
    """

    def __init__(
        self,
        *,
        audio_capture: AudioCapture,
        capabilities: DesktopCapabilities,
        data_dir: Path,
        environment: Mapping[str, str] | None = None,
        minimum_disk_bytes: int = 1_000_000_000,
        minimum_memory_bytes: int = 4_000_000_000,
        provider_checks: Mapping[str, ProviderCheck] | None = None,
    ) -> None:
        self.audio_capture = audio_capture
        self.capabilities = capabilities
        self.data_dir = Path(data_dir)
        self.environment = os.environ if environment is None else environment
        self.minimum_disk_bytes = minimum_disk_bytes
        self.minimum_memory_bytes = minimum_memory_bytes
        self.provider_checks = provider_checks or {}

    def check(self, *, test_providers: bool = False) -> ReadinessReport:
        checks = [
            self._microphone(),
            self._shortcut(),
            self._insertion(),
            self._secret_portal(),
            self._disk(),
            self._memory(),
            self._model_runtime(),
            self._flatpak_permissions(),
        ]
        if test_providers:
            checks.extend(self._providers())
        return ReadinessReport(tuple(checks))

    def _microphone(self) -> ReadinessCheck:
        try:
            devices = self.audio_capture.available_devices()
        except Exception:
            return ReadinessCheck(
                "microphone",
                ReadinessStatus.ACTION_REQUIRED,
                "Microphone",
                "OpenWhisper could not list microphones. Check the selected audio device and "
                "microphone permission.",
            )
        if devices:
            return ReadinessCheck(
                "microphone",
                ReadinessStatus.READY,
                "Microphone",
                f"{len(devices)} microphone(s) available.",
            )
        return ReadinessCheck(
            "microphone",
            ReadinessStatus.ACTION_REQUIRED,
            "Microphone",
            "No microphone is available. Connect or enable one, then reopen OpenWhisper.",
        )

    def _shortcut(self) -> ReadinessCheck:
        if self.capabilities.global_shortcuts_portal:
            message = "Global Shortcuts portal is available; binding requires your confirmation."
            status = ReadinessStatus.READY
        elif self.capabilities.x11_shortcuts:
            message = "X11 global shortcut fallback is available."
            status = ReadinessStatus.READY
        else:
            message = (
                "No global shortcut backend is available; use the tray control or enable a portal."
            )
            status = ReadinessStatus.ACTION_REQUIRED
        return ReadinessCheck("shortcut", status, "Global shortcut", message)

    def _insertion(self) -> ReadinessCheck:
        if self.capabilities.direct_insertion:
            return ReadinessCheck(
                "insertion",
                ReadinessStatus.READY,
                "Text insertion",
                "Direct insertion is available; protected/password fields are always excluded.",
            )
        if self.capabilities.clipboard:
            return ReadinessCheck(
                "insertion",
                ReadinessStatus.READY,
                "Text insertion",
                "Direct insertion is unavailable; OpenWhisper will copy text and notify you.",
            )
        return ReadinessCheck(
            "insertion",
            ReadinessStatus.ACTION_REQUIRED,
            "Text insertion",
            "No direct insertion or clipboard fallback is available.",
        )

    def _secret_portal(self) -> ReadinessCheck:
        if not self.capabilities.flatpak:
            return ReadinessCheck(
                "secret-portal",
                ReadinessStatus.UNKNOWN,
                "Credential storage",
                "Not running in Flatpak; system credential storage may be used for development.",
            )
        if self.capabilities.secret_portal:
            return ReadinessCheck(
                "secret-portal",
                ReadinessStatus.READY,
                "Credential storage",
                "The Secret portal is available for encrypted credential storage.",
            )
        return ReadinessCheck(
            "secret-portal",
            ReadinessStatus.ACTION_REQUIRED,
            "Credential storage",
            "The Secret portal is unavailable. Use an environment variable for this session; "
            "keys will not be persisted.",
        )

    def _disk(self) -> ReadinessCheck:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(self.data_dir).free
        except OSError:
            return ReadinessCheck(
                "disk",
                ReadinessStatus.ACTION_REQUIRED,
                "Disk space",
                "OpenWhisper cannot inspect its data directory. Check storage permissions.",
            )
        status = (
            ReadinessStatus.READY
            if free >= self.minimum_disk_bytes
            else ReadinessStatus.ACTION_REQUIRED
        )
        return ReadinessCheck(
            "disk",
            status,
            "Disk space",
            "Enough free space is available for model downloads."
            if status is ReadinessStatus.READY
            else "Free disk space is low; clear space before downloading a local model.",
        )

    def _memory(self) -> ReadinessCheck:
        memory = _physical_memory_bytes()
        if memory is None:
            return ReadinessCheck(
                "memory",
                ReadinessStatus.UNKNOWN,
                "Memory",
                "OpenWhisper could not determine physical memory; CPU transcription may be slower.",
            )
        status = (
            ReadinessStatus.READY
            if memory >= self.minimum_memory_bytes
            else ReadinessStatus.ACTION_REQUIRED
        )
        return ReadinessCheck(
            "memory",
            status,
            "Memory",
            "Enough memory is available for the supported local runtime."
            if status is ReadinessStatus.READY
            else "Memory is low for the local runtime; use a smaller model or free memory.",
        )

    def _model_runtime(self) -> ReadinessCheck:
        has_whisper = importlib.util.find_spec("faster_whisper") is not None
        has_qt_audio = importlib.util.find_spec("PySide6.QtMultimedia") is not None
        if has_whisper and has_qt_audio:
            status = ReadinessStatus.READY
            message = "Local Faster Whisper and Qt Multimedia are installed."
        else:
            status = ReadinessStatus.ACTION_REQUIRED
            missing = []
            if not has_whisper:
                missing.append("Faster Whisper")
            if not has_qt_audio:
                missing.append("Qt Multimedia")
            message = f"Missing runtime component(s): {', '.join(missing)}. Reinstall OpenWhisper."
        return ReadinessCheck("model-runtime", status, "Local runtime", message)

    def _flatpak_permissions(self) -> ReadinessCheck:
        if not self.capabilities.flatpak:
            return ReadinessCheck(
                "flatpak-permissions",
                ReadinessStatus.UNKNOWN,
                "Flatpak permissions",
                "Not running in Flatpak.",
            )
        if self.environment.get("PULSE_SERVER") or self.environment.get("PIPEWIRE_RUNTIME_DIR"):
            return ReadinessCheck(
                "flatpak-permissions",
                ReadinessStatus.READY,
                "Flatpak permissions",
                "The sandbox has an audio service connection.",
            )
        return ReadinessCheck(
            "flatpak-permissions",
            ReadinessStatus.UNKNOWN,
            "Flatpak permissions",
            "Audio permission will be confirmed when OpenWhisper enumerates the microphone.",
        )

    def _providers(self) -> Sequence[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        for provider, callback in self.provider_checks.items():
            try:
                passed, message = callback()
            except Exception:
                passed, message = False, "Connection test could not complete."
            checks.append(
                ReadinessCheck(
                    f"provider:{provider}",
                    ReadinessStatus.READY if passed else ReadinessStatus.ACTION_REQUIRED,
                    f"{provider} connection",
                    message,
                )
            )
        return tuple(checks)


def _physical_memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None
