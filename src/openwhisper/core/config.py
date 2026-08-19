"""OpenWhisper preferences and a non-destructive legacy configuration migration."""

from __future__ import annotations

import configparser
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

COMPUTE_SELECTIONS = frozenset({"auto", "cpu", "nvidia", "amd"})
OUTPUT_MODES = frozenset({"insert", "clipboard", "both"})


@dataclass(frozen=True, slots=True)
class AppPaths:
    """All application-owned paths derived from a user's XDG directories."""

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    legacy_config: Path
    # A Flatpak has a private XDG_CONFIG_HOME, while the one legacy directory
    # mounted read-only from the host remains visible below HOME/.config.  Keep
    # both locations explicit instead of widening the sandbox to all configs.
    # The empty default preserves the small four-argument constructor used by
    # embedding applications and older tests.
    legacy_config_candidates: tuple[Path, ...] = ()

    @classmethod
    def for_home(cls, home: Path) -> AppPaths:
        home = Path(home)
        return cls(
            config_dir=home / ".config" / "openwhisper",
            data_dir=home / ".local" / "share" / "openwhisper",
            cache_dir=home / ".cache" / "openwhisper",
            legacy_config=home / ".config" / "whisper" / "config.ini",
        )

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> AppPaths:
        """Build paths from XDG variables without creating directories."""

        env = os.environ if environment is None else environment
        home = Path(env.get("HOME") or Path.home())
        config_home = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
        data_home = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
        cache_home = Path(env.get("XDG_CACHE_HOME") or home / ".cache")
        legacy_config = config_home / "whisper" / "config.ini"
        candidates = [legacy_config]
        if env.get("FLATPAK_ID"):
            host_legacy = home / ".config" / "whisper" / "config.ini"
            if host_legacy not in candidates:
                candidates.append(host_legacy)
        return cls(
            config_dir=config_home / "openwhisper",
            data_dir=data_home / "openwhisper",
            cache_dir=cache_home / "openwhisper",
            legacy_config=legacy_config,
            legacy_config_candidates=tuple(candidates),
        )

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.ini"

    @property
    def history_database(self) -> Path:
        return self.data_dir / "history.sqlite3"

    @property
    def audio_temp_dir(self) -> Path:
        return self.cache_dir / "audio"

    @property
    def model_cache_dir(self) -> Path:
        """Engine-owned resumable Faster-Whisper model storage."""

        return self.cache_dir / "models"

    @property
    def retained_audio_dir(self) -> Path:
        return self.data_dir / "retained-audio"

    @property
    def personalization_database(self) -> Path:
        return self.data_dir / "personalization.sqlite3"

    @property
    def credential_envelope(self) -> Path:
        return self.data_dir / "credentials.enc"

    @property
    def migration_marker(self) -> Path:
        return self.config_dir / ".legacy-whisper-migrated"

    @property
    def legacy_migration_sources(self) -> tuple[Path, ...]:
        """Read-only locations that may contain a legacy Whisper config."""

        return self.legacy_config_candidates or (self.legacy_config,)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Non-secret application preferences persisted in ``config.ini``."""

    transcription_provider: str = "faster-whisper"
    transcription_model: str = "large-v3-turbo"
    device: str = "auto"
    compute_type: str = "auto"
    output_mode: str = "insert"
    language: str = "auto"
    cleanup_mode: str = "raw"
    cleanup_provider: str | None = None
    custom_cleanup_prompt: str | None = None
    shortcut: str = "<alt>+o"
    shortcut_mode: str = "toggle"
    notifications: bool = False
    history_retention_days: int = 30
    live_insertion: bool = False
    active_mode_id: str = "raw"
    # Missing values belong to upgraded profiles, which should not be forced
    # through onboarding. RuntimeController stores False for a new profile.
    onboarding_completed: bool = True
    theme: str = "system"
    reduced_motion: bool = False
    retain_audio: bool = False
    audio_retention_days: int = 7
    audio_device_id: str | None = None

    def __post_init__(self) -> None:
        # ``cuda`` was the public spelling before vendor-specific choices were
        # introduced. Normalize it at construction so every caller observes
        # the stable persisted value while old INI files remain readable.
        if self.device == "cuda":
            object.__setattr__(self, "device", "nvidia")
        if self.device not in COMPUTE_SELECTIONS:
            raise ValueError("unsupported compute selection")
        if self.output_mode not in OUTPUT_MODES:
            raise ValueError("unsupported output mode")
        if self.history_retention_days < 0:
            raise ValueError("history_retention_days cannot be negative")
        if not 1 <= self.audio_retention_days <= 30:
            raise ValueError("audio_retention_days must be between 1 and 30")
        if self.cleanup_mode not in {"raw", "clean", "formal", "custom"}:
            raise ValueError("unsupported cleanup mode")
        if self.shortcut_mode not in {"toggle", "push-to-talk"}:
            raise ValueError("unsupported shortcut mode")
        if not self.shortcut.strip():
            raise ValueError("shortcut cannot be empty")
        if not self.active_mode_id.strip():
            raise ValueError("active_mode_id cannot be empty")
        if self.theme not in {"system", "light", "dark"}:
            raise ValueError("unsupported theme")


class ConfigStore:
    """INI storage containing preferences only; credentials stay in portal/env memory."""

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def load(self, *, migrate_legacy: bool = True) -> AppConfig:
        if migrate_legacy:
            # Stop after the first successful migration.  The migration helper
            # also stops subsequent candidates when a destination or marker is
            # already present, which keeps host-mounted Flatpak files strictly
            # read-only.
            for legacy_path in self.paths.legacy_migration_sources:
                if migrate_legacy_config(
                    legacy_path,
                    self.paths.config_file,
                    marker_path=self.paths.migration_marker,
                ):
                    break
                if self.paths.config_file.exists() or self.paths.migration_marker.exists():
                    break
        if not self.paths.config_file.exists():
            return AppConfig()

        parser = _new_parser()
        parser.read(self.paths.config_file, encoding="utf-8")
        defaults = AppConfig()

        # The aliases let early preview configurations (the documented [app]
        # and [dictation] sections) load safely. Saving always writes the
        # compact canonical schema below.
        shortcut_mode = _get_choice(
            parser,
            ("shortcuts", "mode"),
            ("dictation", "mode"),
            default=defaults.shortcut_mode,
            choices={"toggle", "push-to-talk"},
        )
        shortcut_default = (
            _get_text(parser, ("dictation", "push_to_talk_shortcut"), default=defaults.shortcut)
            if shortcut_mode == "push-to-talk"
            else _get_text(parser, ("dictation", "toggle_shortcut"), default=defaults.shortcut)
        )
        config = AppConfig(
            transcription_provider=_canonical_provider(
                _get_text(
                    parser,
                    ("transcription", "provider"),
                    default=defaults.transcription_provider,
                )
            ),
            transcription_model=_get_text(
                parser,
                ("transcription", "model"),
                ("transcription.faster_whisper", "model"),
                default=defaults.transcription_model,
            ),
            device=_get_text(
                parser,
                ("transcription", "device"),
                ("transcription.faster_whisper", "device"),
                default=defaults.device,
            ),
            compute_type=_get_text(
                parser,
                ("transcription", "compute_type"),
                ("transcription.faster_whisper", "compute_type"),
                default=defaults.compute_type,
            ),
            output_mode=_get_choice(
                parser,
                ("general", "output_mode"),
                ("output", "mode"),
                ("dictation", "output_mode"),
                default=defaults.output_mode,
                choices=set(OUTPUT_MODES),
            ),
            language=_get_text(
                parser,
                ("transcription", "language"),
                ("dictation", "language"),
                default=defaults.language,
            ),
            cleanup_mode=_get_choice(
                parser,
                ("cleanup", "mode"),
                ("dictation", "cleanup_mode"),
                default=defaults.cleanup_mode,
                choices={"raw", "clean", "formal", "custom"},
            ),
            cleanup_provider=_get_optional_text(parser, ("cleanup", "provider")),
            custom_cleanup_prompt=_get_optional_text(parser, ("cleanup", "custom_prompt")),
            shortcut=_get_text(parser, ("shortcuts", "dictation"), default=shortcut_default),
            shortcut_mode=shortcut_mode,
            notifications=_get_bool(
                parser,
                ("general", "notifications"),
                ("app", "notifications"),
                default=defaults.notifications,
            ),
            history_retention_days=_get_nonnegative_int(
                parser,
                ("history", "retention_days"),
                ("app", "history_retention_days"),
                default=defaults.history_retention_days,
            ),
            live_insertion=_get_bool(
                parser,
                ("general", "live_insertion"),
                ("dictation", "live_insertion"),
                default=defaults.live_insertion,
            ),
            active_mode_id=_get_text(
                parser,
                ("modes", "active"),
                default=defaults.active_mode_id,
            ),
            onboarding_completed=_get_bool(
                parser,
                ("general", "onboarding_completed"),
                default=defaults.onboarding_completed,
            ),
            theme=_get_choice(
                parser,
                ("appearance", "theme"),
                default=defaults.theme,
                choices={"system", "light", "dark"},
            ),
            reduced_motion=_get_bool(
                parser,
                ("general", "reduced_motion"),
                default=defaults.reduced_motion,
            ),
            retain_audio=_get_bool(
                parser,
                ("audio", "retain"),
                default=defaults.retain_audio,
            ),
            audio_retention_days=_get_bounded_int(
                parser,
                ("audio", "retention_days"),
                default=defaults.audio_retention_days,
                minimum=1,
                maximum=30,
            ),
            audio_device_id=_get_optional_text(parser, ("audio", "device_id")),
        )
        return config

    def save(self, config: AppConfig) -> None:
        parser = _parser_for(config)
        _atomic_write_parser(parser, self.paths.config_file)


def migrate_legacy_config(
    legacy_path: Path,
    destination_path: Path,
    *,
    marker_path: Path | None = None,
) -> bool:
    """Migrate supported legacy preferences once without editing the source.

    The original ``dictate.py`` configuration has no credentials in the
    migrated sections. Unknown keys are intentionally not copied, so a legacy
    file cannot accidentally become a new secret store.
    """

    legacy_path = Path(legacy_path)
    destination_path = Path(destination_path)
    marker_path = (
        Path(marker_path)
        if marker_path is not None
        else destination_path.parent / ".legacy-whisper-migrated"
    )
    if destination_path.exists() or marker_path.exists() or not legacy_path.exists():
        return False

    legacy = _new_parser()
    legacy.read(legacy_path, encoding="utf-8")
    defaults = AppConfig()
    english_only = _get_bool(legacy, ("whisper", "english_only"), default=False)
    legacy_language = _get_text(legacy, ("whisper", "language"), default=defaults.language)
    language = "en" if english_only else legacy_language

    migrated = AppConfig(
        transcription_provider="faster-whisper",
        transcription_model=_get_text(
            legacy, ("whisper", "model"), default=defaults.transcription_model
        ),
        device=_get_text(legacy, ("whisper", "device"), default=defaults.device),
        compute_type=_get_text(legacy, ("whisper", "compute_type"), default=defaults.compute_type),
        language=language,
        shortcut=_get_text(legacy, ("hotkey", "key"), default=defaults.shortcut),
        notifications=_get_bool(
            legacy, ("behavior", "notifications"), default=defaults.notifications
        ),
    )
    _atomic_write_parser(_parser_for(migrated), destination_path)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.touch(exist_ok=True)
    return True


def _new_parser() -> configparser.ConfigParser:
    return configparser.ConfigParser(interpolation=None)


def _parser_for(config: AppConfig) -> configparser.ConfigParser:
    parser = _new_parser()
    parser["general"] = {
        "notifications": str(config.notifications).lower(),
        "live_insertion": str(config.live_insertion).lower(),
        "reduced_motion": str(config.reduced_motion).lower(),
        "onboarding_completed": str(config.onboarding_completed).lower(),
        "output_mode": config.output_mode,
    }
    parser["transcription"] = {
        "provider": config.transcription_provider,
        "model": config.transcription_model,
        "device": config.device,
        "compute_type": config.compute_type,
        "language": config.language,
    }
    parser["cleanup"] = {
        "mode": config.cleanup_mode,
        "provider": config.cleanup_provider or "",
        "custom_prompt": config.custom_cleanup_prompt or "",
    }
    parser["shortcuts"] = {
        "dictation": config.shortcut,
        "mode": config.shortcut_mode,
    }
    parser["history"] = {"retention_days": str(config.history_retention_days)}
    parser["modes"] = {"active": config.active_mode_id}
    parser["appearance"] = {"theme": config.theme}
    parser["audio"] = {
        "retain": str(config.retain_audio).lower(),
        "retention_days": str(config.audio_retention_days),
        "device_id": config.audio_device_id or "",
    }
    return parser


def _get_text(
    parser: configparser.ConfigParser,
    *locations: tuple[str, str],
    default: str,
) -> str:
    for section, option in locations:
        value = parser.get(section, option, fallback=None)
        if value is not None and value.strip():
            return value.strip()
    return default


def _get_optional_text(
    parser: configparser.ConfigParser, *locations: tuple[str, str]
) -> str | None:
    value = _get_text(parser, *locations, default="")
    return value or None


def _get_choice(
    parser: configparser.ConfigParser,
    *locations: tuple[str, str],
    default: str,
    choices: set[str],
) -> str:
    value = _get_text(parser, *locations, default=default)
    return value if value in choices else default


def _canonical_provider(provider: str) -> str:
    """Accept the underscore spelling used by early example configurations."""

    return {"faster_whisper": "faster-whisper"}.get(provider, provider)


def _get_bool(
    parser: configparser.ConfigParser,
    *locations: tuple[str, str],
    default: bool,
) -> bool:
    for section, option in locations:
        try:
            value = parser.getboolean(section, option, fallback=None)
        except ValueError:
            continue
        if value is not None:
            return value
    return default


def _get_nonnegative_int(
    parser: configparser.ConfigParser,
    *locations: tuple[str, str],
    default: int,
) -> int:
    for section, option in locations:
        try:
            value = parser.getint(section, option, fallback=None)
        except ValueError:
            continue
        if value is not None:
            return value if value >= 0 else default
    return default


def _get_bounded_int(
    parser: configparser.ConfigParser,
    *locations: tuple[str, str],
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    for section, option in locations:
        try:
            value = parser.getint(section, option, fallback=None)
        except ValueError:
            continue
        if value is not None:
            return value if minimum <= value <= maximum else default
    return default


def _atomic_write_parser(parser: configparser.ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            parser.write(temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
