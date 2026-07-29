from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from openwhisper import runtime
from openwhisper.core import (
    AppPaths,
    HistoryRecord,
    InsertionMethod,
    InsertionResult,
    ModeDefinition,
    Snippet,
    VocabularyEntry,
)
from openwhisper.runtime import (
    FinalizingTextInserter,
    LiveInsertionState,
    RuntimeController,
    X11PasteBackend,
)


def paths(tmp_path):
    return AppPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        legacy_config=tmp_path / "legacy" / "config.ini",
    )


def test_runtime_defaults_to_private_local_provider(tmp_path):
    controller = RuntimeController(paths=paths(tmp_path), environment={})
    try:
        settings = controller.settings()
        assert settings.transcription_provider == "faster-whisper"
        assert settings.cleanup_mode == "raw"
        assert settings.retention_days == 30
        assert settings.notifications is False
        assert settings.retain_audio is False
        assert settings.audio_retention_days == 7
        assert settings.active_mode_id == "raw"
        assert not controller.has_api_key("openai")
    finally:
        controller.shutdown()


def test_settings_reject_live_insertion_for_cloud_provider(tmp_path):
    controller = RuntimeController(paths=paths(tmp_path), environment={})
    try:
        settings = replace(
            controller.settings(),
            transcription_provider="deepgram",
            transcription_model="nova-3",
            live_insertion=True,
        )
        with pytest.raises(ValueError, match="live insertion"):
            controller.save_settings(settings)
    finally:
        controller.shutdown()


def test_settings_reject_empty_custom_cleanup_instructions(tmp_path):
    controller = RuntimeController(paths=paths(tmp_path), environment={})
    try:
        settings = replace(controller.settings(), cleanup_mode="custom", custom_cleanup_prompt="  ")
        with pytest.raises(ValueError, match="requires instructions"):
            controller.save_settings(settings)
    finally:
        controller.shutdown()


def test_settings_reject_cleanup_that_cannot_rewrite_live_inserted_text(tmp_path):
    controller = RuntimeController(paths=paths(tmp_path), environment={})
    try:
        settings = replace(controller.settings(), cleanup_mode="clean", live_insertion=True)
        with pytest.raises(ValueError, match="raw cleanup"):
            controller.save_settings(settings)
    finally:
        controller.shutdown()


def test_search_history_maps_local_records_for_the_ui(tmp_path):
    controller = RuntimeController(paths=paths(tmp_path), environment={})
    try:
        controller.history.add(
            HistoryRecord(
                raw_text="مرحبا hello",
                final_text="مرحبا hello",
                language="ar",
                transcription_provider="faster-whisper",
                duration_seconds=1.25,
                created_at=datetime.now(UTC),
            )
        )
        rows = controller.search_history("hello")
        assert len(rows) == 1
        assert rows[0].final_text == "مرحبا hello"
        assert rows[0].provider == "faster-whisper"
    finally:
        controller.shutdown()


def test_startup_deletes_only_managed_stale_audio(tmp_path):
    app_paths = paths(tmp_path)
    app_paths.audio_temp_dir.mkdir(parents=True)
    stale = app_paths.audio_temp_dir / "openwhisper-stale.wav"
    unrelated = app_paths.audio_temp_dir / "keep.wav"
    stale.write_bytes(b"old")
    unrelated.write_bytes(b"keep")

    controller = RuntimeController(paths=app_paths, environment={})
    try:
        assert not stale.exists()
        assert unrelated.exists()
    finally:
        controller.shutdown()


class MemoryInserter:
    def __init__(self):
        self.values = []

    def insert(self, text):
        self.values.append(text)
        return InsertionResult(InsertionMethod.X11)


class MemoryClipboard:
    def __init__(self, value):
        self.value = value
        self.copies = []

    def read(self):
        return self.value

    def copy(self, text):
        self.value = text
        self.copies.append(text)


def test_x11_paste_restores_clipboard_only_when_unchanged(monkeypatch):
    clipboard = MemoryClipboard("previous clipboard")
    commands = []
    monkeypatch.setattr(runtime.time, "sleep", lambda _duration: None)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    X11PasteBackend(clipboard).insert("dictated text")

    assert clipboard.copies == ["dictated text", "previous clipboard"]
    assert commands == [["xdotool", "key", "--clearmodifiers", "ctrl+v"]]

    clipboard = MemoryClipboard("another previous value")

    def user_changes_clipboard(_command, **_kwargs):
        clipboard.value = "user changed this"

    monkeypatch.setattr(runtime.subprocess, "run", user_changes_clipboard)
    X11PasteBackend(clipboard).insert("dictated text")
    assert clipboard.copies == ["dictated text"]
    assert clipboard.value == "user changed this"


def test_live_insertion_deduplicates_boundaries_and_final_batch_prefix():
    inserter = MemoryInserter()
    events = []
    state = LiveInsertionState(inserter, lambda event, payload: events.append((event, payload)))
    finalizer = FinalizingTextInserter(inserter, state)

    state.insert_chunk("مرحبا hello")
    state.insert_chunk("hello from OpenWhisper")
    result = finalizer.insert("مرحبا hello from OpenWhisper on Linux")

    assert inserter.values == ["مرحبا hello", " from OpenWhisper", " on Linux"]
    assert events[-1] == (
        "partial",
        {"text": "مرحبا hello from OpenWhisper"},
    )
    assert result.method is InsertionMethod.X11


def test_finalizer_does_not_duplicate_when_live_text_is_already_complete():
    inserter = MemoryInserter()
    state = LiveInsertionState(inserter, lambda *_args: None)
    finalizer = FinalizingTextInserter(inserter, state)
    state.insert_chunk("السلام عليكم")

    result = finalizer.insert("السلام عليكم")

    assert inserter.values == ["السلام عليكم"]
    assert result.method is InsertionMethod.X11


def test_finalizer_aligns_a_corrected_final_transcript_instead_of_counting_words():
    inserter = MemoryInserter()
    state = LiveInsertionState(inserter, lambda *_args: None)
    finalizer = FinalizingTextInserter(inserter, state)
    state.insert_chunk("I like blue cars")

    result = finalizer.insert("I love very blue cars today")

    assert inserter.values == ["I like blue cars", " today"]
    assert result.method is InsertionMethod.X11


def test_runtime_prefers_user_mediated_portal_shortcut_for_toggle(tmp_path, monkeypatch) -> None:
    created = []

    class Transport:
        @property
        def available(self):
            return True

    class Portal:
        def __init__(self, _transport, *, on_error):
            self.on_error = on_error
            self.unregistered = False
            created.append(self)

        def register_many(self, bindings):
            self.bindings = bindings

        def unregister(self):
            self.unregistered = True

    monkeypatch.setattr(runtime, "QtDbusGlobalShortcutsPortalTransport", Transport)
    monkeypatch.setattr(runtime, "PortalGlobalShortcutBackend", Portal)
    controller = RuntimeController(paths=paths(tmp_path), environment={})
    try:
        controller.save_mode(ModeDefinition("coding", "Coding", shortcut="<alt>+c"))
        starts = []
        monkeypatch.setattr(controller, "_start_recording", lambda: starts.append("start"))
        controller.start_shortcut()
        assert len(created) == 1
        assert created[0].bindings["dictation"][0] == "<alt>+o"
        assert created[0].bindings["mode-coding"][0] == "<alt>+c"
        created[0].bindings["mode-coding"][1]()
        assert controller.settings().active_mode_id == "coding"
        assert starts == ["start"]
        assert controller._shortcut_service is None
        controller._restart_shortcut()
        assert created[0].unregistered
        assert len(created) == 2
    finally:
        controller.shutdown()


def test_runtime_persists_modes_vocabulary_and_snippets(tmp_path):
    app_paths = paths(tmp_path)
    controller = RuntimeController(paths=app_paths, environment={})
    try:
        controller.select_mode("message")
        controller.save_vocabulary(VocabularyEntry("ow", "OpenWhisper", ("open whisper",)))
        controller.save_snippet(Snippet("sig", "my signature", "Yousuf"))
        mode = next(item for item in controller.list_modes() if item.id == "message")
        processor = controller._personalization_processor(mode)
        assert processor("open whisper comma my signature") == "OpenWhisper, Yousuf"
    finally:
        controller.shutdown()

    reopened = RuntimeController(paths=app_paths, environment={})
    try:
        assert reopened.settings().active_mode_id == "message"
        assert reopened.list_vocabulary()[0].written_form == "OpenWhisper"
        assert reopened.list_snippets()[0].expansion == "Yousuf"
    finally:
        reopened.shutdown()


def test_runtime_maps_and_deletes_rich_history_metadata(tmp_path):
    controller = RuntimeController(paths=paths(tmp_path), environment={})
    try:
        saved = controller.history.add(
            HistoryRecord(
                raw_text="raw",
                final_text="final",
                language="en",
                transcription_provider="faster-whisper",
                cleanup_provider="local-qwen3",
                cleanup_model="qwen",
                duration_seconds=2,
                mode_id="note",
                latency_ms=140,
                transform_name="polish",
            )
        )
        row = controller.search_history("")[0]
        assert row.mode_id == "note"
        assert row.cleanup_provider == "local-qwen3"
        assert row.latency_ms == 140
        assert row.transform_name == "polish"
        controller.delete_history(saved.id)
        assert controller.search_history("") == ()
    finally:
        controller.shutdown()


def test_command_actions_use_local_history_and_confirmed_mode_changes(tmp_path, monkeypatch):
    controller = RuntimeController(
        paths=paths(tmp_path), environment={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":1"}
    )
    try:
        controller.history.add(
            HistoryRecord(
                raw_text="last raw",
                final_text="last final",
                language="en",
                transcription_provider="test",
                duration_seconds=1,
            )
        )
        copied = []
        monkeypatch.setattr(controller, "copy_text", copied.append)
        controller.copy_last_transcript()
        assert copied == ["last final"]

        controller.apply_configuration_proposal("active_mode", "Message")
        assert controller.settings().active_mode_id == "message"

        commands = []
        monkeypatch.setattr("openwhisper.runtime.shutil.which", lambda command: f"/bin/{command}")
        monkeypatch.setattr(
            "openwhisper.runtime.subprocess.run",
            lambda command, **_kwargs: commands.append(command),
        )
        controller.run_key_action("Enter")
        assert commands == [["xdotool", "key", "--clearmodifiers", "Return"]]
    finally:
        controller.shutdown()
