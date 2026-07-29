"""Composition layer between the provider-neutral core and desktop UI.

No Qt types live in this module.  A future desktop shell can reuse the runtime
and subscribe to the same small event stream.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from .core import (
    AppConfig,
    AppPaths,
    AtspiAccessibilityBackend,
    AudioCaptureConfig,
    AudioDeviceError,
    AudioRetentionPolicy,
    CapabilityDesktopIntegration,
    CleanupMode,
    ConfigStore,
    DesktopSession,
    DesktopTextInserter,
    DictationSession,
    HistoryRecord,
    HistoryStatistics,
    InsertionMethod,
    InsertionResult,
    PortalGlobalShortcutBackend,
    QtDbusGlobalShortcutsPortalTransport,
    QtMultimediaAudioCapture,
    ReadinessChecker,
    RetainedAudioManager,
    SessionBusyError,
    SessionState,
    ShortcutController,
    ShortcutMode,
    SQLiteHistoryStore,
    SQLitePersonalizationStore,
    TempAudioManager,
    apply_snippets,
    apply_vocabulary,
    detect_desktop_capabilities,
    smart_format,
)
from .core.personalization import (
    CleanupStyle,
    DictationContext,
    ModeDefinition,
    ModeRouter,
    TransformDefinition,
    TransformKind,
)
from .providers import (
    LlamaServer,
    LlamaServerConfig,
    LocalEditingPackManager,
    Qwen3LocalCleanupProvider,
    StablePrefixReconciler,
)
from .providers.contracts import (
    CleanupContext,
    CleanupRequest,
    TranscriptionRequest,
)
from .providers.contracts import (
    CleanupMode as ProviderCleanupMode,
)
from .providers.credentials import CredentialStore
from .providers.errors import ProviderError, ProviderErrorKind
from .providers.local_pack import CohereLocalPackManager
from .providers.models import (
    COHERE_LOCAL_ARABIC_MODEL,
    COHERE_TRANSCRIBE_MODEL,
    DEEPGRAM_TRANSCRIBE_MODEL,
    FASTER_WHISPER_DEFAULT_MODEL,
    FASTER_WHISPER_MODELS,
    GROQ_TRANSCRIBE_MODEL,
    OPENAI_TRANSCRIBE_MODEL,
)
from .ui.models import AppSettings, HistoryRow, ProviderOption

EventCallback = Callable[[str, Mapping[str, Any]], None]


class LiveInsertionState:
    """Track raw chunks already inserted so final batch output is not duplicated."""

    def __init__(self, inserter: DesktopTextInserter, emit: EventCallback) -> None:
        self._inserter = inserter
        self._emit = emit
        self._reconciler = StablePrefixReconciler()
        self._last_result: InsertionResult | None = None
        self._lock = threading.RLock()

    @property
    def has_text(self) -> bool:
        with self._lock:
            return bool(self._reconciler.stable_text)

    @property
    def last_result(self) -> InsertionResult | None:
        with self._lock:
            return self._last_result

    def insert_chunk(self, text: str) -> None:
        with self._lock:
            had_text = bool(self._reconciler.stable_text)
            reconciled = self._reconciler.reconcile_chunk(text)
            chunk = reconciled.insertion
            if not chunk:
                return
            insertion_text = f" {chunk}" if had_text else chunk
        result = self._inserter.insert(insertion_text)
        with self._lock:
            self._last_result = result
            preview = self._reconciler.stable_text
        self._emit("partial", {"text": preview})

    def remaining_final_text(self, final_text: str) -> str:
        """Return a conservatively aligned final suffix after live insertion."""

        with self._lock:
            had_text = bool(self._reconciler.stable_text)
            suffix = self._reconciler.reconcile_final(final_text)
        return f" {suffix}" if had_text and suffix else suffix


class FinalizingTextInserter:
    """Core insertion adapter that accounts for live-inserted raw text."""

    def __init__(self, delegate: DesktopTextInserter, state: LiveInsertionState) -> None:
        self._delegate = delegate
        self._state = state

    def insert(self, text: str) -> InsertionResult:
        suffix = self._state.remaining_final_text(text)
        if suffix:
            return self._delegate.insert(suffix)
        return self._state.last_result or InsertionResult(InsertionMethod.CLIPBOARD)


class CommandClipboard:
    """Unicode-safe clipboard adapter for both X11 and Wayland."""

    def __init__(self, session: DesktopSession) -> None:
        self._session = session

    def copy(self, text: str) -> None:
        if self._session is DesktopSession.WAYLAND and shutil.which("wl-copy"):
            command = ["wl-copy", "--type", "text/plain;charset=utf-8"]
        elif shutil.which("xclip"):
            command = ["xclip", "-selection", "clipboard", "-in"]
        elif shutil.which("xsel"):
            command = ["xsel", "--clipboard", "--input"]
        elif shutil.which("wl-copy"):
            command = ["wl-copy", "--type", "text/plain;charset=utf-8"]
        else:
            raise RuntimeError("No supported clipboard command is installed")
        subprocess.run(
            command,
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5,
        )

    def read(self) -> str | None:
        if self._session is DesktopSession.WAYLAND and shutil.which("wl-paste"):
            command = ["wl-paste", "--no-newline", "--type", "text"]
        elif shutil.which("xclip"):
            command = ["xclip", "-selection", "clipboard", "-out"]
        elif shutil.which("xsel"):
            command = ["xsel", "--clipboard", "--output"]
        else:
            return None
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=2,
            )
        except Exception:
            return None
        try:
            return bytes(result.stdout).decode("utf-8")
        except (AttributeError, UnicodeDecodeError):
            return None


class X11PasteBackend:
    """Paste through the X11 clipboard so Arabic/RTL text remains Unicode."""

    def __init__(self, clipboard: CommandClipboard) -> None:
        self._clipboard = clipboard

    def available(self) -> bool:
        return shutil.which("xdotool") is not None and (
            shutil.which("xclip") is not None or shutil.which("xsel") is not None
        )

    def insert(self, text: str) -> None:
        previous = self._clipboard.read()
        self._clipboard.copy(text)
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5,
        )
        # X11 clients normally request the selection immediately after Ctrl+V.
        # Restore only if it still contains our exact text, so a user clipboard
        # change made during insertion always wins.
        if previous is not None:
            time.sleep(0.08)
            if self._clipboard.read() == text:
                self._clipboard.copy(previous)


class WaylandTextBackend:
    """Use a compositor-supported virtual keyboard when one is available."""

    def available(self) -> bool:
        return shutil.which("wtype") is not None

    def insert(self, text: str) -> None:
        subprocess.run(
            ["wtype", "--", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )


class _EventNotifier:
    def __init__(self, emit: EventCallback) -> None:
        self._emit = emit

    def notify(self, title: str, message: str) -> None:
        self._emit("warning", {"title": title, "message": message})


class GlobalShortcutService:
    """Own the pynput listener while keeping gesture semantics in core."""

    def __init__(
        self,
        shortcut: str,
        mode: ShortcutMode,
        controller: ShortcutController,
    ) -> None:
        self.shortcut = shortcut
        self.mode = mode
        self.controller = controller
        self._listener: Any = None

    def start(self) -> None:
        if self._listener is not None:
            return
        from pynput import keyboard

        if self.mode is ShortcutMode.TOGGLE:
            self._listener = keyboard.GlobalHotKeys({self.shortcut: self.controller.pressed})
            self._listener.start()
            return

        required = frozenset(keyboard.HotKey.parse(self.shortcut))
        pressed: set[Any] = set()
        active = False

        def on_press(key: Any) -> None:
            nonlocal active
            canonical = self._listener.canonical(key)
            pressed.add(canonical)
            if not active and required.issubset(pressed):
                active = True
                self.controller.pressed()

        def on_release(key: Any) -> None:
            nonlocal active
            canonical = self._listener.canonical(key)
            if active and canonical in required:
                active = False
                self.controller.released()
            pressed.discard(canonical)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()


class RuntimeController:
    """Application service used by the Qt shell and global shortcut listener."""

    def __init__(
        self,
        *,
        paths: AppPaths | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.environment = os.environ if environment is None else environment
        self._enable_flatpak_extension()
        self.paths = paths or AppPaths.from_environment(self.environment)
        self.config_store = ConfigStore(self.paths)
        self._config = self.config_store.load(migrate_legacy=True)
        self._first_run = not self.paths.config_file.exists()
        if self._first_run:
            # Persist privacy-first defaults so readiness onboarding is shown
            # once, even when the user does not change a setting immediately.
            self.config_store.save(self._config)
        self.credentials = CredentialStore(
            environment=self.environment,
            storage_path=self.paths.credential_envelope,
        )
        self.local_pack = CohereLocalPackManager(self.paths.data_dir)
        self.local_editing_pack = LocalEditingPackManager(self.paths.data_dir)
        self._local_editing_provider: Qwen3LocalCleanupProvider | None = None
        self.personalization = SQLitePersonalizationStore(self.paths.personalization_database)
        self._active_mode_id = self._config.active_mode_id
        self.temporary_audio = TempAudioManager(self.paths.audio_temp_dir)
        self.temporary_audio.cleanup_stale()
        self.audio_retention = RetainedAudioManager(
            self.paths.audio_temp_dir,
            self.paths.retained_audio_dir,
            AudioRetentionPolicy(
                enabled=self._config.retain_audio,
                days=self._config.audio_retention_days,
            ),
        )
        self.history = SQLiteHistoryStore(
            self.paths.history_database,
            retention_days=self._config.history_retention_days,
            retained_audio_dir=self.paths.retained_audio_dir,
        )
        self.history.prune()
        self._callbacks: list[EventCallback] = []
        self._callbacks_lock = threading.RLock()
        self._session: DictationSession | None = None
        self._processing_thread: threading.Thread | None = None
        self._shortcut_service: GlobalShortcutService | None = None
        self._shortcut_services: list[GlobalShortcutService] = []
        self._portal_shortcut: PortalGlobalShortcutBackend | None = None
        self._live_capture: QtMultimediaAudioCapture | None = None
        self._live_provider: Any = None
        self._live_state: LiveInsertionState | None = None
        self._live_stop = threading.Event()
        self._live_thread: threading.Thread | None = None
        self._closed = False

    def _enable_flatpak_extension(self) -> None:
        root = self.environment.get("OPENWHISPER_COHERE_LOCAL_EXTENSION")
        if not root:
            return
        extension = os.path.abspath(root)
        if not os.path.isdir(extension):
            return
        candidates = (
            sorted(
                os.path.join(extension, "lib", entry, "site-packages")
                for entry in os.listdir(os.path.join(extension, "lib"))
                if entry.startswith("python")
            )
            if os.path.isdir(os.path.join(extension, "lib"))
            else []
        )
        for candidate in candidates:
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.insert(0, candidate)

    def subscribe(self, callback: EventCallback) -> None:
        with self._callbacks_lock:
            self._callbacks.append(callback)

    def _emit(self, event: str, payload: Mapping[str, Any] | None = None) -> None:
        with self._callbacks_lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback(event, payload or {})
            except Exception:
                # UI/extension observers must not break audio deletion or history.
                continue

    def settings(self) -> AppSettings:
        config = self._config
        return AppSettings(
            transcription_provider=config.transcription_provider,
            transcription_model=config.transcription_model,
            device=config.device,
            language=config.language,
            cleanup_mode=config.cleanup_mode,
            cleanup_provider=config.cleanup_provider or "none",
            custom_cleanup_prompt=config.custom_cleanup_prompt or "",
            shortcut_mode=config.shortcut_mode,
            shortcut=config.shortcut,
            live_insertion=config.live_insertion,
            retention_days=config.history_retention_days,
            notifications=config.notifications,
            active_mode_id=config.active_mode_id,
            reduced_motion=config.reduced_motion,
            retain_audio=config.retain_audio,
            audio_retention_days=config.audio_retention_days,
            audio_device_id=config.audio_device_id,
        )

    def is_first_run(self) -> bool:
        return self._first_run

    def providers(self) -> Sequence[ProviderOption]:
        local_cohere_available = all(
            importlib.util.find_spec(module) is not None
            for module in ("torch", "transformers", "huggingface_hub")
        )
        return (
            ProviderOption(
                "faster-whisper",
                "Faster Whisper",
                "Local, private transcription and the only live-insertion backend in v0.1.",
                FASTER_WHISPER_MODELS,
                supports_streaming=True,
            ),
            ProviderOption(
                "cohere-local",
                "Cohere Arabic (local pack)",
                "Optional gated 2B Arabic model. Select Arabic or English explicitly.",
                (COHERE_LOCAL_ARABIC_MODEL,),
                available=local_cohere_available,
                unavailable_reason=(
                    None if local_cohere_available else "Optional local pack not installed"
                ),
            ),
            ProviderOption(
                "cohere",
                "Cohere",
                "BYOK Cohere Arabic cloud transcription and optional cleanup.",
                (COHERE_TRANSCRIBE_MODEL,),
                needs_api_key=True,
                supports_cleanup=True,
            ),
            ProviderOption(
                "openai",
                "OpenAI",
                "BYOK GPT Transcribe for multilingual audio and code-switching.",
                (OPENAI_TRANSCRIBE_MODEL,),
                needs_api_key=True,
                supports_cleanup=True,
            ),
            ProviderOption(
                "groq",
                "Groq",
                "BYOK hosted Whisper transcription and optional cleanup.",
                (GROQ_TRANSCRIBE_MODEL,),
                needs_api_key=True,
                supports_cleanup=True,
            ),
            ProviderOption(
                "deepgram",
                "Deepgram",
                "BYOK Nova-3 transcription with Arabic locale hints.",
                (DEEPGRAM_TRANSCRIBE_MODEL,),
                needs_api_key=True,
            ),
            ProviderOption(
                "local-qwen3",
                "Qwen3 4B editing (local)",
                "Optional Apache-2.0 Q4_K_M editing pack; prompts stay on this device.",
                ("Qwen3-4B-GGUF-Q4_K_M",),
                supports_transcription=False,
                supports_cleanup=True,
            ),
        )

    def audio_devices(self) -> Sequence[Any]:
        return QtMultimediaAudioCapture(self.temporary_audio).available_devices()

    def test_microphone(self, device_id: str | None = None) -> tuple[bool, str]:
        capture = QtMultimediaAudioCapture(
            self.temporary_audio,
            default_config=AudioCaptureConfig(device_id=device_id),
        )
        try:
            capture.start()
            capture.cancel()
        except AudioDeviceError as exc:
            return False, str(exc)
        except Exception:
            return False, "The selected microphone could not be opened."
        return True, "The selected microphone opened successfully."

    def save_settings(self, settings: AppSettings) -> None:
        if self._session is not None and self._session.state in {
            SessionState.RECORDING,
            SessionState.PROCESSING,
            SessionState.CLEANING,
            SessionState.INSERTING,
        }:
            raise SessionBusyError("finish or cancel the current dictation first")
        capabilities = {option.id: option for option in self.providers()}
        provider = capabilities.get(settings.transcription_provider)
        if provider is None or not provider.supports_transcription:
            raise ValueError("unknown transcription provider")
        if not provider.available:
            raise ValueError(provider.unavailable_reason or "provider is unavailable")
        if settings.live_insertion and not provider.supports_streaming:
            raise ValueError("live insertion is not supported by this provider")
        if settings.live_insertion and settings.cleanup_mode != "raw":
            raise ValueError("live insertion currently requires raw cleanup mode")
        if settings.transcription_provider == "cohere-local" and settings.language not in {
            "ar",
            "en",
        }:
            raise ValueError("the local Cohere Arabic pack requires language ar or en")
        if settings.cleanup_mode == "custom" and not settings.custom_cleanup_prompt.strip():
            raise ValueError("custom cleanup mode requires instructions")
        cleanup_option = capabilities.get(settings.cleanup_provider)
        if settings.cleanup_provider not in {"", "none"} and (
            cleanup_option is None or not cleanup_option.supports_cleanup
        ):
            raise ValueError("unknown cleanup provider")
        selected_mode = ModeRouter(self.personalization.modes()).route(
            selected_id=settings.active_mode_id
        )
        if settings.live_insertion and selected_mode.cleanup_style is not CleanupStyle.RAW:
            raise ValueError("live insertion requires the Raw mode")
        config = AppConfig(
            transcription_provider=settings.transcription_provider,
            transcription_model=settings.transcription_model or FASTER_WHISPER_DEFAULT_MODEL,
            device=settings.device,
            compute_type=self._config.compute_type,
            language=settings.language,
            cleanup_mode=settings.cleanup_mode,
            cleanup_provider=(
                None if settings.cleanup_provider in {"", "none"} else settings.cleanup_provider
            ),
            custom_cleanup_prompt=settings.custom_cleanup_prompt or None,
            shortcut=settings.shortcut,
            shortcut_mode=settings.shortcut_mode,
            notifications=settings.notifications,
            history_retention_days=settings.retention_days,
            live_insertion=settings.live_insertion,
            active_mode_id=settings.active_mode_id,
            reduced_motion=settings.reduced_motion,
            retain_audio=settings.retain_audio,
            audio_retention_days=settings.audio_retention_days,
            audio_device_id=settings.audio_device_id,
        )
        self.config_store.save(config)
        self._config = config
        self.history.retention_days = config.history_retention_days
        self.history.prune()
        self.audio_retention.policy = AudioRetentionPolicy(
            enabled=config.retain_audio,
            days=config.audio_retention_days,
        )
        self._active_mode_id = config.active_mode_id
        self._restart_shortcut()

    def save_api_key(self, provider: str, api_key: str) -> None:
        self.credentials.set(provider, api_key)

    def has_api_key(self, provider: str) -> bool:
        try:
            return self.credentials.has(provider)
        except ValueError:
            return False

    def test_provider(self, provider: str) -> tuple[bool, str]:
        option = next((item for item in self.providers() if item.id == provider), None)
        if option is None:
            return False, "Unknown provider."
        if not option.available:
            return False, option.unavailable_reason or "Provider is unavailable."
        if provider == "cohere-local":
            return self.local_pack_status()
        if provider == "local-qwen3":
            installed, message = self.local_editing_pack_status()
            if not installed:
                return False, message
            try:
                result = self._local_cleanup_provider().test_connection()
            except ProviderError as exc:
                return False, str(exc)
            return result.ok, result.message
        if option.needs_api_key and not self.has_api_key(provider):
            return False, "Add an API key or set the provider environment variable first."
        try:
            adapter = self._provider_router().transcription(
                provider,
                model=option.models[0] if option.models else None,
                device=self._config.device,
                compute_type=self._config.compute_type,
            )
            result = adapter.test_connection()
        except ProviderError as exc:
            return False, str(exc)
        except Exception:
            return False, "The provider backend could not be initialized."
        return result.ok, result.message

    def local_pack_status(self) -> tuple[bool, str]:
        status = self.local_pack.status()
        return status.installed and status.hardware_supported, status.message

    def install_local_pack(self, token: str | None = None) -> tuple[bool, str]:
        try:
            status = self.local_pack.install(token=token)
        except ProviderError as exc:
            return False, str(exc)
        return status.installed, status.message

    def local_editing_pack_status(self) -> tuple[bool, str]:
        status = self.local_editing_pack.status()
        return status.installed and status.hardware_supported, status.message

    def install_local_editing_pack(self) -> tuple[bool, str]:
        try:
            status = self.local_editing_pack.install()
        except ProviderError as exc:
            return False, str(exc)
        return status.installed and status.hardware_supported, status.message

    def search_history(self, query: str) -> Sequence[HistoryRow]:
        return tuple(
            HistoryRow(
                id=record.id or "",
                created_at=record.created_at,  # type: ignore[arg-type]
                raw_text=record.raw_text,
                final_text=record.final_text,
                provider=record.transcription_provider,
                duration_seconds=record.duration_seconds,
                language=record.language,
                mode_id=record.mode_id,
                cleanup_provider=record.cleanup_provider,
                latency_ms=record.latency_ms,
                transform_name=record.transform_name,
                has_retained_audio=record.has_retained_audio,
            )
            for record in self.history.search(query)
        )

    def list_modes(self) -> Sequence[ModeDefinition]:
        return self.personalization.modes()

    def save_mode(self, mode: ModeDefinition) -> None:
        if mode.live_insertion and mode.cleanup_style is not CleanupStyle.RAW:
            raise ValueError("live insertion requires the Raw cleanup style")
        live_provider = mode.transcription_provider or self._config.transcription_provider
        if mode.live_insertion and live_provider != "faster-whisper":
            raise ValueError("live insertion currently requires Faster-Whisper")
        self.personalization.save_mode(mode)
        if self._shortcut_services or self._portal_shortcut is not None:
            self._restart_shortcut()

    def delete_mode(self, identifier: str) -> None:
        self.personalization.delete_mode(identifier)
        if self._active_mode_id == identifier:
            self.select_mode("raw")
        if self._shortcut_services or self._portal_shortcut is not None:
            self._restart_shortcut()

    def select_mode(self, mode_id: str) -> None:
        mode = ModeRouter(self.personalization.modes()).route(selected_id=mode_id)
        live_insertion = self._config.live_insertion or mode.live_insertion
        if live_insertion and mode.cleanup_style is not CleanupStyle.RAW:
            raise ValueError("live insertion requires the Raw mode")
        live_provider = mode.transcription_provider or self._config.transcription_provider
        if live_insertion and live_provider != "faster-whisper":
            raise ValueError("live insertion currently requires Faster-Whisper")
        self._active_mode_id = mode_id
        self._config = replace(self._config, active_mode_id=mode_id)
        self.config_store.save(self._config)

    def list_vocabulary(self) -> Sequence[Any]:
        return self.personalization.vocabulary()

    def save_vocabulary(self, entry: Any) -> None:
        self.personalization.save_vocabulary(entry)

    def delete_vocabulary(self, identifier: str) -> None:
        self.personalization.delete_vocabulary(identifier)

    def export_vocabulary(self, *, format: str = "json") -> str:
        return self.personalization.export_vocabulary(format=format)

    def import_vocabulary(self, source: str, *, format: str = "json") -> Sequence[Any]:
        return self.personalization.import_vocabulary(source, format=format)

    def list_snippets(self) -> Sequence[Any]:
        return self.personalization.snippets()

    def save_snippet(self, snippet: Any) -> None:
        self.personalization.save_snippet(snippet)

    def delete_snippet(self, identifier: str) -> None:
        self.personalization.delete_snippet(identifier)

    def export_snippets(self, *, format: str = "json") -> str:
        return self.personalization.export_snippets(format=format)

    def import_snippets(self, source: str, *, format: str = "json") -> Sequence[Any]:
        return self.personalization.import_snippets(source, format=format)

    def list_transforms(self) -> Sequence[Any]:
        return self.personalization.transforms()

    def save_transform(self, transform: Any) -> None:
        self.personalization.save_transform(transform)

    def selected_text(self) -> str:
        accessibility = AtspiAccessibilityBackend()
        target = accessibility.focused_target()
        if target is None or target.protected or not target.supports_selection:
            return ""
        return accessibility.selected_text() or ""

    def replace_selected_text(self, text: str) -> bool:
        if not text:
            raise ValueError("replacement text cannot be empty")
        return self._desktop_integration().replace_selection(text)

    def insert_text(self, text: str) -> None:
        if not text.strip():
            raise ValueError("text cannot be empty")
        self._desktop_integration().insert(text)

    def transform_text(self, text: str, transform: TransformDefinition) -> str:
        provider = self._editing_provider()
        instruction = transform.instruction
        if transform.kind is TransformKind.TRANSLATE:
            instruction = (
                f"Translate to {transform.target_language}. "
                "Preserve meaning, names, and formatting."
            )
        result = provider.cleanup(
            CleanupRequest(
                raw_text=text,
                mode=ProviderCleanupMode.CUSTOM,
                custom_instruction=instruction,
                language_hint=self._config.language,
            )
        )
        return result.text

    def run_text_command(self, instruction: str, *, selected_text: str = "") -> str:
        prompt = instruction.strip()
        if not prompt:
            raise ValueError("an editing instruction is required")
        source = selected_text.strip() or "Create the requested text."
        if not selected_text.strip():
            prompt = f"Generate text for this request: {prompt}"
        result = self._editing_provider().cleanup(
            CleanupRequest(
                raw_text=source,
                mode=ProviderCleanupMode.CUSTOM,
                custom_instruction=prompt,
                language_hint=self._config.language,
            )
        )
        return result.text

    def _editing_provider(self) -> Any:
        provider_id = self._config.cleanup_provider
        if not provider_id and self.local_editing_pack.status().installed:
            provider_id = "local-qwen3"
        if not provider_id:
            raise ValueError("Choose an editing provider before using this action")
        return (
            self._local_cleanup_provider()
            if provider_id == "local-qwen3"
            else self._provider_router().cleanup(provider_id)
        )

    def delete_history(self, identifier: str) -> None:
        if not self.history.delete(identifier):
            raise KeyError(identifier)

    def clear_history(self) -> None:
        self.history.clear()

    def history_statistics(self) -> HistoryStatistics:
        return self.history.statistics()

    def retry_history(self, identifier: str) -> None:
        record = self.history.get(identifier)
        if record is None:
            raise KeyError(identifier)
        if not record.has_retained_audio:
            raise ValueError("Retained audio is unavailable for this transcript")
        threading.Thread(
            target=self._retry_history_worker,
            args=(identifier,),
            name="openwhisper-history-retry",
            daemon=True,
        ).start()

    def reclean_history(self, identifier: str) -> None:
        record = self.history.get(identifier)
        if record is None:
            raise KeyError(identifier)
        threading.Thread(
            target=self._reclean_history_worker,
            args=(identifier,),
            name="openwhisper-history-cleanup",
            daemon=True,
        ).start()

    def readiness_checks(self) -> Mapping[str, str]:
        capabilities = detect_desktop_capabilities(self.environment)
        checker = ReadinessChecker(
            audio_capture=QtMultimediaAudioCapture(self.temporary_audio),
            capabilities=capabilities,
            data_dir=self.paths.data_dir,
            environment=self.environment,
        )
        report = checker.check()
        return {check.title: f"{check.status.value} — {check.message}" for check in report.checks}

    def _retry_history_worker(self, identifier: str) -> None:
        try:
            record = self.history.get(identifier)
            if record is None or not record.has_retained_audio:
                raise ValueError("Retained audio is unavailable for this transcript")
            mode = ModeRouter(self.personalization.modes()).route(selected_id=record.mode_id)
            provider = self._provider_router().transcription(
                mode.transcription_provider or self._config.transcription_provider,
                model=mode.transcription_model or self._config.transcription_model,
                device=self._config.device,
                compute_type=self._config.compute_type,
            )
            result = provider.transcribe(
                TranscriptionRequest(
                    audio_path=record.retained_audio_path,  # type: ignore[arg-type]
                    language=record.language,
                    recognition_hints=tuple(
                        entry.written_form for entry in self.personalization.vocabulary()
                    )[:100],
                )
            )
            final_text, cleanup_name, cleanup_model = self._clean_existing_text(result.text, mode)
            final_text = self._personalization_processor(mode)(final_text)
            insertion = self._desktop_integration().insert(final_text)
            saved = self.history.add(
                HistoryRecord(
                    raw_text=result.text,
                    final_text=final_text,
                    language=result.language,
                    transcription_provider=result.provider,
                    cleanup_provider=cleanup_name,
                    cleanup_model=cleanup_model,
                    duration_seconds=result.duration_seconds,
                    mode_id=mode.id,
                    insertion_method=insertion.method.value,
                    transform_name="retry",
                )
            )
            self._emit("transcript", {"text": final_text, "history_id": saved.id or ""})
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, ProviderError)) else "Retry failed."
            self._emit("error", {"message": message})

    def _reclean_history_worker(self, identifier: str) -> None:
        try:
            record = self.history.get(identifier)
            if record is None:
                raise KeyError(identifier)
            mode = ModeRouter(self.personalization.modes()).route(selected_id=record.mode_id)
            final_text, cleanup_name, cleanup_model = self._clean_existing_text(
                record.raw_text, mode
            )
            final_text = self._personalization_processor(mode)(final_text)
            self.history.update_final_text(
                identifier,
                final_text,
                transform_name="re-clean",
                cleanup_provider=cleanup_name,
                cleanup_model=cleanup_model,
            )
            self._emit("history", {"id": identifier, "action": "re-cleaned"})
        except Exception as exc:
            message = (
                str(exc) if isinstance(exc, (ValueError, ProviderError)) else "Re-clean failed."
            )
            self._emit("error", {"message": message})

    def _clean_existing_text(
        self, text: str, mode: ModeDefinition
    ) -> tuple[str, str | None, str | None]:
        from .providers import CoreCleanupAdapter

        cleanup_mode, custom_prompt = self._cleanup_settings_for_mode(mode)
        provider_id = mode.cleanup_provider or self._config.cleanup_provider
        if cleanup_mode is CleanupMode.RAW or not provider_id:
            return text, None, None
        provider = (
            self._local_cleanup_provider()
            if provider_id == "local-qwen3"
            else self._provider_router().cleanup(provider_id, model=mode.cleanup_model)
        )
        cloud = provider_id in {"cohere", "openai", "groq"}
        adapter = CoreCleanupAdapter(
            provider,
            language_hint=mode.language,
            context=self._cleanup_context(mode, cloud=cloud),
        )
        cleaned = adapter.cleanup(
            text,
            mode=cleanup_mode.value,
            custom_prompt=custom_prompt,
        )
        return cleaned, provider.name, provider.model

    def copy_text(self, text: str) -> None:
        session = DesktopSession.from_environment(self.environment)
        CommandClipboard(session).copy(text)

    def copy_last_transcript(self) -> None:
        record = self.history.last_transcript()
        if record is None:
            raise ValueError("History is empty")
        self.copy_text(record.final_text or record.raw_text)

    def paste_last_transcript(self) -> None:
        record = self.history.last_transcript()
        if record is None:
            raise ValueError("History is empty")
        self._desktop_integration().insert(record.final_text or record.raw_text)

    def run_key_action(self, key: str) -> None:
        if key.casefold() not in {"enter", "return"}:
            raise ValueError("unsupported key action")
        session = DesktopSession.from_environment(self.environment)
        if session is DesktopSession.X11 and shutil.which("xdotool"):
            command = ["xdotool", "key", "--clearmodifiers", "Return"]
        elif session is DesktopSession.WAYLAND and shutil.which("wtype"):
            command = ["wtype", "-k", "Return"]
        else:
            raise RuntimeError("No secure key-action backend is available")
        subprocess.run(command, check=True, timeout=5)

    def apply_configuration_proposal(self, key: str, value: str) -> None:
        if key != "active_mode":
            raise ValueError("unsupported configuration proposal")
        candidate = value.casefold().replace(" ", "-")
        modes = self.personalization.modes()
        mode = next(
            (
                item
                for item in modes
                if item.id == candidate or item.name.casefold() == value.casefold()
            ),
            None,
        )
        if mode is None:
            raise ValueError(f"Unknown mode: {value}")
        self.select_mode(mode.id)

    def toggle_recording(self) -> None:
        if self._closed:
            return
        if self._session is not None and self._session.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        if self._processing_thread is not None and self._processing_thread.is_alive():
            self._emit("warning", {"message": "The previous dictation is still processing."})
            return
        try:
            self._session = self._build_session()
            self._session.start_recording()
            if self._live_capture is not None:
                self._start_live_worker()
        except SessionBusyError as exc:
            self._emit("warning", {"message": str(exc)})
        except FileNotFoundError:
            self._emit(
                "error",
                {
                    "message": (
                        "Audio capture is unavailable. Check your microphone and "
                        "Flatpak permission."
                    )
                },
            )
        except AudioDeviceError as exc:
            self._emit("error", {"message": str(exc)})
        except ProviderError as exc:
            self._emit("error", {"message": str(exc)})
        except Exception:
            self._emit("error", {"message": "OpenWhisper could not start recording."})

    def _stop_recording(self) -> None:
        if self._session is None:
            return
        self._emit("state", {"state": SessionState.PROCESSING.value})
        self._processing_thread = threading.Thread(
            target=self._process_recording,
            name="openwhisper-processing",
            daemon=True,
        )
        self._processing_thread.start()

    def _process_recording(self) -> None:
        assert self._session is not None
        try:
            self._stop_live_worker(join=True)
            outcome = self._session.stop_and_process()
            for warning in outcome.warnings:
                self._emit("warning", {"message": warning})
            if outcome.final_text:
                self._emit(
                    "transcript",
                    {
                        "text": outcome.final_text,
                        "raw_text": outcome.raw_text,
                        "provider": (
                            outcome.transcript.provider if outcome.transcript is not None else ""
                        ),
                    },
                )
        except ProviderError as exc:
            self._emit("error", {"message": str(exc)})
        except Exception:
            self._emit(
                "error",
                {"message": "Dictation failed. The temporary audio has been deleted."},
            )
        finally:
            self._processing_thread = None
            self._live_capture = None
            self._live_provider = None
            self._live_state = None

    def cancel(self) -> None:
        self._stop_live_worker(join=False)
        if self._session is not None:
            self._session.cancel()

    def _session_state_changed(self, state: SessionState) -> None:
        self._emit("state", {"state": state.value})

    def _audio_level_changed(self, event: Any) -> None:
        self._emit(
            "audio-level",
            {"rms": event.rms, "peak": event.peak, "elapsed": event.elapsed_seconds},
        )

    def _build_session(self) -> DictationSession:
        from .providers import CoreCleanupAdapter, CoreTranscriptionAdapter

        router = self._provider_router()
        cancellation = threading.Event()

        def progress(event: Any) -> None:
            self._emit(
                "provider-progress",
                {
                    "provider": event.provider,
                    "stage": event.stage.value,
                    "fraction": event.fraction,
                    "message": event.message,
                },
            )

        mode = self._selected_mode()
        provider_id = mode.transcription_provider or self._config.transcription_provider
        live_insertion = mode.live_insertion or self._config.live_insertion
        model = mode.transcription_model or self._config.transcription_model
        language = mode.language if mode.language != "auto" else self._config.language
        if provider_id == "cohere-local":
            pack_status = self.local_pack.status()
            if not pack_status.installed or not pack_status.hardware_supported:
                raise ProviderError(
                    "cohere-local",
                    kind=ProviderErrorKind.CONFIGURATION,
                    message="Install the managed local Cohere model pack first",
                )
            model = str(pack_status.path)
        provider = router.transcription(
            provider_id,
            model=model,
            device=self._config.device,
            compute_type=self._config.compute_type,
        )
        hints = tuple(
            dict.fromkeys(
                value
                for entry in self.personalization.vocabulary()
                for value in (entry.written_form, *entry.spoken_forms)
            )
        )[:100]
        transcription = CoreTranscriptionAdapter(
            provider,
            language=language,
            recognition_hints=hints,
            cancellation=cancellation,
            progress=progress,
        )
        cleanup = None
        cleanup_mode, custom_prompt = self._cleanup_settings_for_mode(mode)
        cleanup_provider_id = mode.cleanup_provider or self._config.cleanup_provider
        if cleanup_mode is not CleanupMode.RAW and cleanup_provider_id:
            cleanup_provider = (
                self._local_cleanup_provider()
                if cleanup_provider_id == "local-qwen3"
                else router.cleanup(cleanup_provider_id, model=mode.cleanup_model)
            )
            cloud = cleanup_provider_id in {"cohere", "openai", "groq"}
            cleanup = CoreCleanupAdapter(
                cleanup_provider,
                language_hint=language,
                context=self._cleanup_context(mode, cloud=cloud),
                cancellation=cancellation,
                progress=progress,
            )

        inserter = self._desktop_integration()
        audio_capture: QtMultimediaAudioCapture
        text_inserter: Any = inserter
        self._live_capture = None
        self._live_provider = None
        self._live_state = None
        if live_insertion:
            self._live_capture = QtMultimediaAudioCapture(
                self.temporary_audio,
                level_listener=self._audio_level_changed,
                default_config=AudioCaptureConfig(device_id=self._config.audio_device_id),
            )
            self._live_provider = provider
            self._live_state = LiveInsertionState(inserter, self._emit)
            audio_capture = self._live_capture
            text_inserter = FinalizingTextInserter(inserter, self._live_state)
        else:
            audio_capture = QtMultimediaAudioCapture(
                self.temporary_audio,
                level_listener=self._audio_level_changed,
                default_config=AudioCaptureConfig(device_id=self._config.audio_device_id),
            )
        return DictationSession(
            audio_capture=audio_capture,
            temporary_audio=self.temporary_audio,
            transcription_provider=transcription,
            text_inserter=text_inserter,
            history=self.history,
            cleanup_mode=cleanup_mode,
            cleanup_provider=cleanup,
            custom_cleanup_prompt=custom_prompt,
            mode_id=mode.id,
            final_text_processor=self._personalization_processor(mode),
            audio_retention=self.audio_retention,
            cancellation_event=cancellation,
            state_listener=self._session_state_changed,
        )

    def _selected_mode(self) -> ModeDefinition:
        modes = self.personalization.modes()
        router = ModeRouter(modes)
        selected = router.route(selected_id=self._active_mode_id)
        accessibility = AtspiAccessibilityBackend()
        application = accessibility.application_id()
        activated = router.route(
            application=application,
            site=accessibility.site_identifier(),
        )
        if activated.activation_rules:
            return activated
        return selected

    def _cleanup_settings_for_mode(self, mode: ModeDefinition) -> tuple[CleanupMode, str | None]:
        if mode.cleanup_style is CleanupStyle.RAW:
            return CleanupMode.RAW, None
        if mode.cleanup_style is CleanupStyle.CLEAN:
            return CleanupMode.CLEAN, None
        if mode.cleanup_style is CleanupStyle.FORMAL:
            return CleanupMode.FORMAL, None
        instructions = {
            CleanupStyle.MESSAGE: "Format as a concise natural message without adding facts.",
            CleanupStyle.EMAIL: "Format as a complete email without inventing names or details.",
            CleanupStyle.NOTE: "Format as structured notes and lists where clearly implied.",
            CleanupStyle.SMART: "Use the consented context only to resolve formatting and tone.",
            CleanupStyle.CUSTOM: mode.custom_instruction or "",
        }
        return CleanupMode.CUSTOM, instructions[mode.cleanup_style]

    def _personalization_processor(self, mode: ModeDefinition) -> Callable[[str], str]:
        vocabulary = self.personalization.vocabulary()
        snippets = self.personalization.snippets()

        def process(text: str) -> str:
            result = apply_vocabulary(text, vocabulary)
            result = apply_snippets(result, snippets)
            if mode.cleanup_style in {
                CleanupStyle.CLEAN,
                CleanupStyle.MESSAGE,
                CleanupStyle.EMAIL,
                CleanupStyle.NOTE,
                CleanupStyle.SMART,
            }:
                result = smart_format(result)
            return result

        return process

    def _desktop_integration(self) -> CapabilityDesktopIntegration:
        desktop = DesktopSession.from_environment(self.environment)
        clipboard = CommandClipboard(desktop)
        inserter = DesktopTextInserter(
            session=desktop,
            clipboard=clipboard,
            x11=X11PasteBackend(clipboard),
            wayland=WaylandTextBackend(),
            notifier=_EventNotifier(self._emit),
        )
        return CapabilityDesktopIntegration(
            inserter=inserter,
            capabilities=detect_desktop_capabilities(self.environment),
            accessibility=AtspiAccessibilityBackend(),
        )

    def _cleanup_context(self, mode: ModeDefinition, *, cloud: bool) -> CleanupContext | None:
        if cloud and not mode.context_policy.allow_cloud:
            return None
        integration = self._desktop_integration()
        context: DictationContext = integration.collect_context(
            mode.context_policy,
            cloud=cloud,
        )
        content = context.content_for(mode.context_policy, cloud=cloud)
        return CleanupContext.from_content(
            content,
            application_name=context.application_name,
        )

    def _local_cleanup_provider(self) -> Qwen3LocalCleanupProvider:
        status = self.local_editing_pack.status()
        if not status.installed:
            raise ProviderError(
                "local-qwen3",
                ProviderErrorKind.CONFIGURATION,
                "Install the managed local Qwen3 editing pack first",
            )
        if self._local_editing_provider is None:
            server = LlamaServer(
                LlamaServerConfig(
                    self.local_editing_pack.model_path,
                    experimental_gpu=(
                        self.environment.get("OPENWHISPER_EXPERIMENTAL_LOCAL_GPU") == "1"
                    ),
                )
            )
            self._local_editing_provider = Qwen3LocalCleanupProvider(server)
        return self._local_editing_provider

    def _start_live_worker(self) -> None:
        if self._live_capture is None or self._live_provider is None or self._live_state is None:
            return
        self._live_stop.clear()
        self._live_thread = threading.Thread(
            target=self._run_live_worker,
            name="openwhisper-live-transcription",
            daemon=True,
        )
        self._live_thread.start()

    def _run_live_worker(self) -> None:
        capture = self._live_capture
        provider = self._live_provider
        state = self._live_state
        if capture is None or provider is None or state is None:
            return
        language = None if self._config.language == "auto" else self._config.language
        while not self._live_stop.wait(0.35):
            chunk = None
            try:
                chunk = capture.take_chunk(minimum_duration_seconds=1.5)
                if chunk is None:
                    continue
                request = TranscriptionRequest(audio_path=chunk.path, language=language)
                results = provider.transcribe_stream((request,))
                for result in results:
                    state.insert_chunk(result.text)
            except Exception:
                if not self._live_stop.is_set():
                    self._emit(
                        "warning",
                        {
                            "message": (
                                "Live insertion paused; final transcription will run "
                                "after you stop."
                            )
                        },
                    )
                return
            finally:
                if chunk is not None:
                    self.temporary_audio.delete(chunk.path)

    def _stop_live_worker(self, *, join: bool) -> None:
        self._live_stop.set()
        thread = self._live_thread
        if join and thread is not None and thread is not threading.current_thread():
            thread.join()
        if thread is None or not thread.is_alive():
            self._live_thread = None

    def _provider_router(self):
        from .providers import ProviderRouter

        return ProviderRouter(credentials=self.credentials)

    def start_shortcut(self) -> None:
        if self._shortcut_services or self._portal_shortcut is not None:
            return
        shortcut_mode = ShortcutMode(self._config.shortcut_mode)
        specs = self._shortcut_specs(shortcut_mode)

        # The portal is user-mediated and is therefore the only Wayland global
        # shortcut path. Activated/Deactivated drive push-to-talk without a
        # direct host keyboard listener.
        transport = QtDbusGlobalShortcutsPortalTransport()
        if transport.available:
            portal = PortalGlobalShortcutBackend(transport, on_error=self._portal_shortcut_error)
            try:
                portal.register_many(
                    {
                        identifier: (
                            shortcut,
                            gesture.pressed,
                            gesture.released
                            if shortcut_mode is ShortcutMode.PUSH_TO_TALK
                            else None,
                        )
                        for identifier, shortcut, gesture in specs
                    }
                )
            except Exception:
                # A synchronous portal failure may still have an X11 fallback.
                pass
            else:
                self._portal_shortcut = portal
                return

        self._start_x11_shortcuts(shortcut_mode, specs)

    def _portal_shortcut_error(self, message: str) -> None:
        self._portal_shortcut = None
        self._emit("warning", {"message": message})
        if DesktopSession.from_environment(self.environment) is not DesktopSession.X11:
            return
        shortcut_mode = ShortcutMode(self._config.shortcut_mode)
        self._start_x11_shortcuts(shortcut_mode, self._shortcut_specs(shortcut_mode))

    def _shortcut_specs(
        self, shortcut_mode: ShortcutMode
    ) -> list[tuple[str, str, ShortcutController]]:
        specs: list[tuple[str, str, ShortcutController]] = []
        seen: set[str] = set()

        def add(identifier: str, shortcut: str, mode_id: str | None = None) -> None:
            normalized = shortcut.strip().casefold()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)

            def start() -> None:
                if mode_id is not None:
                    self.select_mode(mode_id)
                self._start_recording()

            specs.append(
                (
                    identifier,
                    shortcut,
                    ShortcutController(
                        shortcut_mode,
                        start_recording=start,
                        stop_recording=self._stop_recording,
                        is_recording=lambda: bool(self._session and self._session.is_recording),
                    ),
                )
            )

        add("dictation", self._config.shortcut)
        for mode in self.personalization.modes():
            if mode.shortcut:
                add(f"mode-{mode.id}", mode.shortcut, mode.id)
        return specs

    def _start_x11_shortcuts(
        self,
        shortcut_mode: ShortcutMode,
        specs: Sequence[tuple[str, str, ShortcutController]],
    ) -> None:
        if DesktopSession.from_environment(self.environment) is not DesktopSession.X11:
            self._emit(
                "warning",
                {
                    "message": (
                        "The Global Shortcuts portal is unavailable; use the tray control in this "
                        "desktop session."
                    )
                },
            )
            return
        services: list[GlobalShortcutService] = []
        try:
            for _identifier, shortcut, gesture in specs:
                service = GlobalShortcutService(shortcut, shortcut_mode, gesture)
                service.start()
                services.append(service)
        except Exception:
            for service in services:
                service.stop()
            self._emit(
                "warning",
                {
                    "message": (
                        "One or more global shortcuts could not be registered; use the tray window."
                    )
                },
            )
            return
        self._shortcut_services = services
        self._shortcut_service = services[0] if services else None

    def _restart_shortcut(self) -> None:
        if not self._shortcut_services and self._portal_shortcut is None:
            return
        for service in self._shortcut_services:
            service.stop()
        self._shortcut_services.clear()
        self._shortcut_service = None
        if self._portal_shortcut is not None:
            self._portal_shortcut.unregister()
            self._portal_shortcut = None
        self.start_shortcut()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for service in self._shortcut_services:
            service.stop()
        self._shortcut_services.clear()
        self._shortcut_service = None
        if self._portal_shortcut is not None:
            self._portal_shortcut.unregister()
            self._portal_shortcut = None
        self._stop_live_worker(join=False)
        if self._session is not None:
            self._session.cancel()
        if self._local_editing_provider is not None:
            self._local_editing_provider.close()
            self._local_editing_provider = None
        self.personalization.close()
        self.history.close()
