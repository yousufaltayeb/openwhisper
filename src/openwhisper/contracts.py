"""Shell-neutral data contracts shared by the Qt and Tauri frontends.

These types deliberately have no Qt imports.  The private engine serializes
them for the Tauri host while the temporary Qt parity shell imports the same
objects directly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any, Protocol


class ThemePreference(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class OutputMode(StrEnum):
    """Where a completed transcript is delivered."""

    INSERT = "insert"
    CLIPBOARD = "clipboard"
    BOTH = "both"


class ComputeSelection(StrEnum):
    """User-visible accelerator selection.

    ``compute_type`` remains a provider-internal precision setting.  This
    value selects the execution backend, not quantization.
    """

    AUTO = "auto"
    CPU = "cpu"
    NVIDIA = "nvidia"
    AMD = "amd"


@dataclass(frozen=True, slots=True)
class HistoryRow:
    id: str
    created_at: datetime
    raw_text: str
    final_text: str
    provider: str
    duration_seconds: float
    language: str | None = None
    mode_id: str = "raw"
    cleanup_provider: str | None = None
    latency_ms: int | None = None
    transform_name: str | None = None
    has_retained_audio: bool = False
    inserted: bool = False
    copied: bool = False


@dataclass(frozen=True, slots=True)
class ProviderOption:
    id: str
    name: str
    description: str
    models: tuple[str, ...] = ()
    supports_streaming: bool = False
    needs_api_key: bool = False
    available: bool = True
    unavailable_reason: str | None = None
    supports_transcription: bool = True
    supports_cleanup: bool = False


@dataclass(slots=True)
class AppSettings:
    transcription_provider: str = "faster-whisper"
    transcription_model: str = "large-v3-turbo"
    device: str = "auto"
    output_mode: str = OutputMode.INSERT.value
    language: str = "auto"
    cleanup_mode: str = "raw"
    cleanup_provider: str = "none"
    custom_cleanup_prompt: str = ""
    shortcut_mode: str = "toggle"
    shortcut: str = "<alt>+o"
    live_insertion: bool = False
    retention_days: int = 30
    notifications: bool = False
    active_mode_id: str = "raw"
    onboarding_completed: bool = True
    theme: str = ThemePreference.SYSTEM.value
    reduced_motion: bool = False
    retain_audio: bool = False
    audio_retention_days: int = 7
    audio_device_id: str | None = None

    def __post_init__(self) -> None:
        if self.device == "cuda":
            self.device = ComputeSelection.NVIDIA.value
        if self.device not in {item.value for item in ComputeSelection}:
            raise ValueError("unsupported compute selection")
        if self.output_mode not in {item.value for item in OutputMode}:
            raise ValueError("unsupported output mode")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> AppSettings:
        known = cls.__dataclass_fields__
        translated = {key: value for key, value in values.items() if key in known}
        # Settings received from an older host may not have delivery mode yet.
        # Keep this migration at the contract boundary as well as in INI
        # storage so browser/test adapters remain backwards compatible.
        translated.setdefault("output_mode", OutputMode.INSERT.value)
        if translated.get("device") == "cuda":
            translated["device"] = ComputeSelection.NVIDIA.value
        return cls(**translated)

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def to_wire(value: Any) -> Any:
    """Convert a contract value to JSON-safe camelCase data.

    Dates are always UTC ISO-8601 strings and enum values remain stable strings.
    This is intentionally strict enough for IPC while still accepting the core
    dataclasses returned by existing runtime services.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return to_wire(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {_camel(field.name): to_wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {_camel(str(key)): to_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_wire(item) for item in value]
    raise TypeError(f"unsupported contract value: {type(value).__name__}")


def from_wire_keys(values: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one level of public camelCase keys to Python snake_case."""

    return {_snake(str(key)): value for key, value in values.items()}


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _snake(value: str) -> str:
    output: list[str] = []
    for character in value:
        if character.isupper():
            output.extend(("_", character.lower()))
        else:
            output.append(character)
    return "".join(output).lstrip("_")


class AppController(Protocol):
    """The complete shell-independent application service boundary."""

    def settings(self) -> AppSettings: ...

    def is_first_run(self) -> bool: ...

    def providers(self) -> Sequence[ProviderOption]: ...

    def audio_devices(self) -> Sequence[Any]: ...

    def test_microphone(self, device_id: str | None = None) -> tuple[bool, str]: ...

    def save_settings(self, settings: AppSettings) -> None: ...

    def select_mode(self, mode_id: str) -> None: ...

    def toggle_recording(self) -> None: ...

    def start_recording(self) -> None: ...

    def stop_recording(self) -> None: ...

    def cancel(self) -> None: ...

    def start_shortcut(self) -> None: ...

    def shutdown(self) -> None: ...

    def subscribe(self, callback: Callable[[str, Mapping[str, Any]], None]) -> None: ...
