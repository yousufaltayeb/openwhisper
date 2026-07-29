"""UI-facing data structures and controller boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


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
    reduced_motion: bool = False
    retain_audio: bool = False
    audio_retention_days: int = 7
    audio_device_id: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> AppSettings:
        known = cls.__dataclass_fields__
        return cls(**{key: value for key, value in values.items() if key in known})

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class AppController(Protocol):
    """The complete interface consumed by the Qt shell."""

    def settings(self) -> AppSettings: ...

    def is_first_run(self) -> bool: ...

    def providers(self) -> Sequence[ProviderOption]: ...

    def audio_devices(self) -> Sequence[Any]: ...

    def test_microphone(self, device_id: str | None = None) -> tuple[bool, str]: ...

    def save_settings(self, settings: AppSettings) -> None: ...

    def save_api_key(self, provider: str, api_key: str) -> None: ...

    def has_api_key(self, provider: str) -> bool: ...

    def test_provider(self, provider: str) -> tuple[bool, str]: ...

    def local_pack_status(self) -> tuple[bool, str]: ...

    def install_local_pack(self, token: str | None = None) -> tuple[bool, str]: ...

    def local_editing_pack_status(self) -> tuple[bool, str]: ...

    def install_local_editing_pack(self) -> tuple[bool, str]: ...

    def search_history(self, query: str) -> Sequence[HistoryRow]: ...

    # Personalization methods are optional during migration.  The Qt shell
    # discovers them by capability so an older runtime remains usable while the
    # new local store is initialized.
    def list_modes(self) -> Sequence[Any]: ...

    def save_mode(self, mode: Any) -> None: ...

    def delete_mode(self, identifier: str) -> None: ...

    def list_vocabulary(self) -> Sequence[Any]: ...

    def save_vocabulary(self, entry: Any) -> None: ...

    def delete_vocabulary(self, identifier: str) -> None: ...

    def list_snippets(self) -> Sequence[Any]: ...

    def save_snippet(self, snippet: Any) -> None: ...

    def delete_snippet(self, identifier: str) -> None: ...

    def export_snippets(self, *, format: str = "json") -> str: ...

    def import_snippets(self, source: str, *, format: str = "json") -> Sequence[Any]: ...

    def list_transforms(self) -> Sequence[Any]: ...

    def save_transform(self, transform: Any) -> None: ...

    def selected_text(self) -> str: ...

    def replace_selected_text(self, text: str) -> bool: ...

    def transform_text(self, text: str, transform: Any) -> str: ...

    def run_text_command(self, instruction: str, *, selected_text: str = "") -> str: ...

    def insert_text(self, text: str) -> None: ...

    def delete_history(self, identifier: str) -> None: ...

    def clear_history(self) -> None: ...

    def history_statistics(self) -> Any: ...

    def retry_history(self, identifier: str) -> None: ...

    def reclean_history(self, identifier: str) -> None: ...

    def copy_text(self, text: str) -> None: ...

    def copy_last_transcript(self) -> None: ...

    def paste_last_transcript(self) -> None: ...

    def run_key_action(self, key: str) -> None: ...

    def apply_configuration_proposal(self, key: str, value: str) -> None: ...

    def toggle_recording(self) -> None: ...

    def cancel(self) -> None: ...

    def shutdown(self) -> None: ...

    def subscribe(self, callback: Callable[[str, Mapping[str, Any]], None]) -> None: ...
