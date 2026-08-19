from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openwhisper.core.history import HistoryRecord, SQLiteHistoryStore


def record(text: str, created_at: datetime | None = None) -> HistoryRecord:
    return HistoryRecord(
        raw_text=text,
        final_text=text,
        language="en",
        transcription_provider="test",
        duration_seconds=1.5,
        created_at=created_at,
    )


def test_history_retention_search_and_reopen(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    now = datetime.now(UTC)
    with SQLiteHistoryStore(database, retention_days=2) as store:
        store.add(record("older transcript", now - timedelta(days=3)))
        stored = store.add(record("100%_ sure", now))
        assert [item.id for item in store.search("100%_")] == [stored.id]
        assert store.search("older") == []

    with SQLiteHistoryStore(database, retention_days=2) as reopened:
        assert reopened.search()[0].final_text == "100%_ sure"


def test_history_recovers_malformed_database_without_deleting_backup(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    database.write_bytes(b"this is not sqlite")

    with SQLiteHistoryStore(database) as store:
        assert store.recovery_backup is not None
        assert store.recovery_backup.read_bytes() == b"this is not sqlite"
        store.add(record("recovered"))
        assert store.search()[0].raw_text == "recovered"


def test_history_adds_optional_columns_to_an_early_schema(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE history (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, raw_text TEXT NOT NULL,
        final_text TEXT NOT NULL, language TEXT, transcription_provider TEXT NOT NULL,
        duration_seconds REAL NOT NULL
        )"""
    )
    connection.commit()
    connection.close()

    with SQLiteHistoryStore(database) as store:
        saved = store.add(record("migrated schema"))
        assert store.search()[0].id == saved.id
        assert store.schema_version == 4


def test_history_filters_updates_statistics_and_recovers_last_transcript(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    with SQLiteHistoryStore(database) as store:
        first = store.add(
            HistoryRecord(
                raw_text="مرحبا hello",
                final_text="مرحبا hello",
                language="ar",
                transcription_provider="faster-whisper",
                duration_seconds=2.5,
                mode_id="message",
                latency_ms=120,
            )
        )
        store.add(
            HistoryRecord(
                raw_text="an email note",
                final_text="An email note.",
                language="en",
                transcription_provider="openai",
                duration_seconds=3.5,
                mode_id="email",
                latency_ms=280,
            )
        )

        assert [item.id for item in store.search("مرحبا", mode_id="message")] == [first.id]
        assert store.search(provider="openai")[0].mode_id == "email"
        updated = store.update_final_text(first.id, "مرحبا بالعالم", transform_name="polish")
        assert updated.transform_name == "polish"
        assert store.last_transcript().final_text == "An email note."
        stats = store.statistics()
        assert stats.transcript_count == 2
        assert stats.word_count == 5
        assert stats.dictated_seconds == 6
        assert stats.average_latency_ms == 200


def test_history_deletion_and_expiry_remove_only_managed_audio(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    audio_dir = tmp_path / "retained-audio"
    audio_dir.mkdir()
    managed = audio_dir / "openwhisper-audio-first.wav"
    managed.write_bytes(b"audio")
    unrelated = tmp_path / "keep.wav"
    unrelated.write_bytes(b"keep")
    now = datetime.now(UTC)

    with SQLiteHistoryStore(database, retained_audio_dir=audio_dir) as store:
        saved = store.add(
            HistoryRecord(
                raw_text="retained",
                final_text="retained",
                language="en",
                transcription_provider="test",
                duration_seconds=1,
                retained_audio_path=managed,
                retained_audio_expires_at=now + timedelta(days=7),
            )
        )
        assert store.search(has_audio=True)[0].id == saved.id
        assert store.delete(saved.id)
        assert not managed.exists()
        assert unrelated.read_bytes() == b"keep"

        expired = audio_dir / "openwhisper-audio-expired.wav"
        expired.write_bytes(b"expired")
        stored_expired = store.add(
            HistoryRecord(
                raw_text="expired",
                final_text="expired",
                language="en",
                transcription_provider="test",
                duration_seconds=1,
                retained_audio_path=expired,
                retained_audio_expires_at=now - timedelta(seconds=1),
            )
        )
        assert store.prune_audio(now=now) == 1
        assert not expired.exists()
        assert store.get(stored_expired.id).retained_audio_path is None


def test_history_rejects_audio_outside_its_managed_directory(tmp_path: Path) -> None:
    outside = tmp_path / "openwhisper-audio-outside.wav"
    outside.write_bytes(b"audio")
    with SQLiteHistoryStore(tmp_path / "history.sqlite3") as store:
        with pytest.raises(ValueError, match="outside OpenWhisper storage"):
            store.add(
                HistoryRecord(
                    raw_text="unsafe",
                    final_text="unsafe",
                    language="en",
                    transcription_provider="test",
                    duration_seconds=1,
                    retained_audio_path=outside,
                )
            )


def test_history_delivery_flags_are_independent_and_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    with SQLiteHistoryStore(database) as store:
        saved = store.add(record("delivered"))
        store.update_delivery(saved.id, inserted=True, copied=True, insertion_method="x11")
        assert store.get(saved.id).inserted
        assert store.get(saved.id).copied

    with SQLiteHistoryStore(database) as reopened:
        restored = reopened.get(saved.id)
        assert restored is not None
        assert restored.inserted is True
        assert restored.copied is True
