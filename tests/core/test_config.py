from __future__ import annotations

from pathlib import Path

from openwhisper.core.config import AppConfig, AppPaths, ConfigStore, migrate_legacy_config


def test_legacy_migration_copies_supported_preferences_once(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy" / "config.ini"
    legacy.parent.mkdir()
    legacy.write_text(
        """[whisper]
model = medium
device = cuda
compute_type = float16
language = ar
english_only = false
api_key = must-not-be-copied
[hotkey]
key = <ctrl>+space
[behavior]
notifications = false
""",
        encoding="utf-8",
    )
    destination = tmp_path / "new" / "config.ini"
    marker = tmp_path / "new" / ".migrated"

    assert migrate_legacy_config(legacy, destination, marker_path=marker)
    assert marker.exists()
    assert not migrate_legacy_config(legacy, destination, marker_path=marker)
    assert "api_key" not in destination.read_text(encoding="utf-8")
    assert "must-not-be-copied" in legacy.read_text(encoding="utf-8")

    store = ConfigStore(AppPaths(destination.parent, tmp_path / "data", tmp_path / "cache", legacy))
    config = store.load(migrate_legacy=False)
    assert config == AppConfig(
        transcription_model="medium",
        device="cuda",
        compute_type="float16",
        language="ar",
        shortcut="<ctrl>+space",
        notifications=False,
    )


def test_config_store_reads_documented_aliases_and_recovers_invalid_values(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        """[app]
history_retention_days = invalid
notifications = false
[dictation]
mode = push-to-talk
push_to_talk_shortcut = <ctrl>+<alt>+space
live_insertion = true
cleanup_mode = formal
[transcription]
provider = faster_whisper
[transcription.faster_whisper]
model = small
""",
        encoding="utf-8",
    )

    config = ConfigStore(paths).load(migrate_legacy=False)

    assert config.shortcut == "<ctrl>+<alt>+space"
    assert config.shortcut_mode == "push-to-talk"
    assert config.transcription_provider == "faster-whisper"
    assert config.transcription_model == "small"
    assert config.history_retention_days == 30
    assert config.notifications is False
    assert config.live_insertion is True


def test_flatpak_can_migrate_read_only_host_legacy_config_once(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sandbox_config = home / ".var" / "app" / "io.github.yousufaltayeb.OpenWhisper" / "config"
    host_legacy = home / ".config" / "whisper" / "config.ini"
    host_legacy.parent.mkdir(parents=True)
    host_legacy.write_text("[whisper]\nmodel = small\n", encoding="utf-8")
    original = host_legacy.read_text(encoding="utf-8")

    paths = AppPaths.from_environment(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(sandbox_config),
            "XDG_DATA_HOME": str(
                home / ".var" / "app" / "io.github.yousufaltayeb.OpenWhisper" / "data"
            ),
            "XDG_CACHE_HOME": str(
                home / ".var" / "app" / "io.github.yousufaltayeb.OpenWhisper" / "cache"
            ),
            "FLATPAK_ID": "io.github.yousufaltayeb.OpenWhisper",
        }
    )

    config = ConfigStore(paths).load()
    assert config.transcription_model == "small"
    assert paths.config_file.exists()
    assert paths.migration_marker.exists()
    assert host_legacy.read_text(encoding="utf-8") == original


def test_config_defaults_keep_notifications_context_and_audio_private(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path)
    config = ConfigStore(paths).load(migrate_legacy=False)

    assert config.notifications is False
    assert config.active_mode_id == "raw"
    assert config.retain_audio is False
    assert config.audio_retention_days == 7
    assert paths.personalization_database.parent == paths.data_dir
    assert paths.retained_audio_dir.parent == paths.data_dir


def test_audio_retention_and_active_mode_round_trip_with_bounds(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path)
    store = ConfigStore(paths)
    expected = AppConfig(
        active_mode_id="message",
        reduced_motion=True,
        retain_audio=True,
        audio_retention_days=14,
        audio_device_id="opaque-qt-device-id",
    )
    store.save(expected)
    assert store.load(migrate_legacy=False) == expected

    paths.config_file.write_text("[audio]\nretain = true\nretention_days = 31\n", encoding="utf-8")
    assert store.load(migrate_legacy=False).audio_retention_days == 7
