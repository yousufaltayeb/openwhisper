"""Versioned local history with search, recovery, and safe audio retention."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 4


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    raw_text: str
    final_text: str
    language: str | None
    transcription_provider: str
    duration_seconds: float
    cleanup_provider: str | None = None
    warning: str | None = None
    mode_id: str = "raw"
    cleanup_model: str | None = None
    latency_ms: int | None = None
    transform_name: str | None = None
    insertion_method: str | None = None
    retained_audio_path: Path | None = None
    retained_audio_expires_at: datetime | None = None
    created_at: datetime | None = None
    id: str | None = None
    # Appended after the original fields so positional embedding callers keep
    # their pre-delivery constructor shape.
    inserted: bool = False
    copied: bool = False

    def __post_init__(self) -> None:
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if not isinstance(self.inserted, bool) or not isinstance(self.copied, bool):
            raise ValueError("delivery flags must be boolean")
        if not self.mode_id.strip():
            raise ValueError("mode_id cannot be empty")
        for timestamp in (self.created_at, self.retained_audio_expires_at):
            if timestamp is not None:
                _as_utc(timestamp)
        if self.retained_audio_path is not None:
            object.__setattr__(self, "retained_audio_path", Path(self.retained_audio_path))

    @property
    def has_retained_audio(self) -> bool:
        if self.retained_audio_path is None or not self.retained_audio_path.is_file():
            return False
        expiry = self.retained_audio_expires_at
        return expiry is None or _as_utc(expiry) > datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class HistoryStatistics:
    transcript_count: int
    word_count: int
    dictated_seconds: float
    average_latency_ms: float | None


class SQLiteHistoryStore:
    """SQLite history that never stores credentials or captured context.

    Existing preview databases are migrated in place. Genuinely malformed or
    incompatible databases are moved aside for manual recovery. Retained audio
    is optional and may only live in ``retained_audio_dir``; deletion never
    follows symlinks or reaches outside that directory.
    """

    _required_columns = {
        "id",
        "created_at",
        "raw_text",
        "final_text",
        "language",
        "transcription_provider",
        "duration_seconds",
    }
    _optional_columns = {
        "cleanup_provider": "TEXT",
        "warning": "TEXT",
        "mode_id": "TEXT NOT NULL DEFAULT 'raw'",
        "cleanup_model": "TEXT",
        "latency_ms": "INTEGER",
        "transform_name": "TEXT",
        "insertion_method": "TEXT",
        "inserted": "INTEGER NOT NULL DEFAULT 0",
        "copied": "INTEGER NOT NULL DEFAULT 0",
        "retained_audio_path": "TEXT",
        "retained_audio_expires_at": "TEXT",
    }

    def __init__(
        self,
        path: Path,
        *,
        retention_days: int = 30,
        retained_audio_dir: Path | None = None,
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days cannot be negative")
        self.path = Path(path)
        self.retention_days = retention_days
        self.retained_audio_dir = Path(retained_audio_dir or self.path.parent / "retained-audio")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._recovery_backup: Path | None = None
        self._fts_available = False
        self._connection = self._open_connection_with_recovery()
        self.prune()
        self.prune_audio()

    @property
    def recovery_backup(self) -> Path | None:
        return self._recovery_backup

    @property
    def schema_version(self) -> int:
        with self._lock:
            self._ensure_open()
            return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    def add(self, record: HistoryRecord) -> HistoryRecord:
        created_at = _as_utc(record.created_at or datetime.now(UTC))
        audio_path = self._validated_audio_path(record.retained_audio_path)
        stored = replace(
            record,
            id=record.id or str(uuid.uuid4()),
            created_at=created_at,
            retained_audio_path=audio_path,
        )
        with self._lock:
            self._ensure_open()
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO history (
                        id, created_at, raw_text, final_text, language,
                        transcription_provider, cleanup_provider, duration_seconds, warning,
                        mode_id, cleanup_model, latency_ms, transform_name, insertion_method,
                        inserted, copied, retained_audio_path, retained_audio_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _record_values(stored),
                )
                self._prune_locked(datetime.now(UTC), self.retention_days)
        return stored

    def get(self, record_id: str) -> HistoryRecord | None:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT * FROM history WHERE id = ?", (record_id,)
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def search(
        self,
        query: str = "",
        *,
        limit: int = 100,
        mode_id: str | None = None,
        provider: str | None = None,
        language: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        has_audio: bool | None = None,
    ) -> list[HistoryRecord]:
        if limit <= 0:
            return []
        clauses: list[str] = []
        parameters: list[object] = []
        source = "history AS h"
        tokens = _fts_query(query)
        if tokens and self._fts_available:
            source += " JOIN history_fts ON history_fts.rowid = h.rowid"
            clauses.append("history_fts MATCH ?")
            parameters.append(tokens)
        elif query.strip():
            escaped = _escape_like(query.strip())
            clauses.append("(h.raw_text LIKE ? ESCAPE '\\' OR h.final_text LIKE ? ESCAPE '\\')")
            pattern = f"%{escaped}%"
            parameters.extend((pattern, pattern))
        if mode_id:
            clauses.append("h.mode_id = ?")
            parameters.append(mode_id)
        if provider:
            clauses.append("h.transcription_provider = ?")
            parameters.append(provider)
        if language:
            clauses.append("h.language = ?")
            parameters.append(language)
        if created_after is not None:
            clauses.append("h.created_at >= ?")
            parameters.append(_as_utc(created_after).isoformat())
        if created_before is not None:
            clauses.append("h.created_at <= ?")
            parameters.append(_as_utc(created_before).isoformat())
        if has_audio is True:
            clauses.append("h.retained_audio_path IS NOT NULL")
        elif has_audio is False:
            clauses.append("h.retained_audio_path IS NULL")

        sql = f"SELECT h.* FROM {source}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY h.created_at DESC, h.id DESC LIMIT ?"
        parameters.append(limit)
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(sql, parameters).fetchall()
        return [_row_to_record(row) for row in rows]

    def update_final_text(
        self,
        record_id: str,
        final_text: str,
        *,
        transform_name: str | None = None,
        cleanup_provider: str | None = None,
        cleanup_model: str | None = None,
    ) -> HistoryRecord:
        if not final_text.strip():
            raise ValueError("final_text cannot be empty")
        with self._lock:
            self._ensure_open()
            with self._connection:
                cursor = self._connection.execute(
                    """UPDATE history SET final_text = ?, transform_name = ?,
                    cleanup_provider = COALESCE(?, cleanup_provider),
                    cleanup_model = COALESCE(?, cleanup_model) WHERE id = ?""",
                    (final_text, transform_name, cleanup_provider, cleanup_model, record_id),
                )
            if cursor.rowcount != 1:
                raise KeyError(record_id)
        record = self.get(record_id)
        if record is None:  # pragma: no cover - guarded by the update lock
            raise KeyError(record_id)
        return record

    def attach_audio(
        self,
        record_id: str,
        path: Path,
        *,
        expires_at: datetime,
    ) -> HistoryRecord:
        audio_path = self._validated_audio_path(path)
        if audio_path is None or not audio_path.is_file():
            raise ValueError("retained audio must be an existing managed file")
        expiry = _as_utc(expires_at)
        with self._lock:
            self._ensure_open()
            with self._connection:
                cursor = self._connection.execute(
                    """UPDATE history SET retained_audio_path = ?,
                    retained_audio_expires_at = ? WHERE id = ?""",
                    (str(audio_path), expiry.isoformat(), record_id),
                )
            if cursor.rowcount != 1:
                raise KeyError(record_id)
        record = self.get(record_id)
        if record is None:  # pragma: no cover
            raise KeyError(record_id)
        return record

    def update_insertion_method(self, record_id: str, method: str) -> HistoryRecord:
        if not method.strip():
            raise ValueError("insertion method cannot be empty")
        with self._lock:
            self._ensure_open()
            with self._connection:
                cursor = self._connection.execute(
                    """UPDATE history SET insertion_method = ?,
                    inserted = CASE WHEN ? IN ('atspi', 'x11', 'wayland') THEN 1 ELSE inserted END,
                    copied = CASE WHEN ? = 'clipboard' THEN 1 ELSE copied END
                    WHERE id = ?""",
                    (method, method, method, record_id),
                )
            if cursor.rowcount != 1:
                raise KeyError(record_id)
        record = self.get(record_id)
        if record is None:  # pragma: no cover
            raise KeyError(record_id)
        return record

    def update_delivery(
        self,
        record_id: str,
        *,
        inserted: bool,
        copied: bool,
        insertion_method: str | None = None,
    ) -> HistoryRecord:
        """Persist independent insertion and clipboard outcomes."""

        if not isinstance(inserted, bool) or not isinstance(copied, bool):
            raise ValueError("delivery flags must be boolean")
        with self._lock:
            self._ensure_open()
            with self._connection:
                cursor = self._connection.execute(
                    """UPDATE history SET inserted = ?, copied = ?,
                    insertion_method = COALESCE(?, insertion_method) WHERE id = ?""",
                    (int(inserted), int(copied), insertion_method, record_id),
                )
            if cursor.rowcount != 1:
                raise KeyError(record_id)
        record = self.get(record_id)
        if record is None:  # pragma: no cover - guarded by the update lock
            raise KeyError(record_id)
        return record

    def delete(self, record_id: str) -> bool:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT retained_audio_path FROM history WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return False
            with self._connection:
                self._connection.execute("DELETE FROM history WHERE id = ?", (record_id,))
        self._delete_audio(row["retained_audio_path"])
        return True

    def clear(self) -> int:
        with self._lock:
            self._ensure_open()
            paths = [
                row[0]
                for row in self._connection.execute(
                    "SELECT retained_audio_path FROM history WHERE retained_audio_path IS NOT NULL"
                )
            ]
            with self._connection:
                cursor = self._connection.execute("DELETE FROM history")
        for path in paths:
            self._delete_audio(path)
        return cursor.rowcount

    def last_transcript(self) -> HistoryRecord | None:
        rows = self.search(limit=1)
        return rows[0] if rows else None

    def statistics(self) -> HistoryStatistics:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                """SELECT COUNT(*) AS transcript_count,
                COALESCE(SUM(duration_seconds), 0) AS dictated_seconds,
                AVG(latency_ms) AS average_latency_ms FROM history"""
            ).fetchone()
            texts = self._connection.execute("SELECT final_text FROM history").fetchall()
        return HistoryStatistics(
            transcript_count=int(row["transcript_count"]),
            word_count=sum(len(item[0].split()) for item in texts),
            dictated_seconds=float(row["dictated_seconds"]),
            average_latency_ms=(
                float(row["average_latency_ms"]) if row["average_latency_ms"] is not None else None
            ),
        )

    def prune(
        self,
        *,
        now: datetime | None = None,
        retention_days: int | None = None,
    ) -> int:
        days = self.retention_days if retention_days is None else retention_days
        if days < 0:
            raise ValueError("retention_days cannot be negative")
        current = _as_utc(now or datetime.now(UTC))
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                "SELECT retained_audio_path FROM history WHERE created_at < ?",
                ((current - timedelta(days=days)).isoformat(),),
            ).fetchall()
            with self._connection:
                deleted = self._prune_locked(current, days)
        for row in rows:
            self._delete_audio(row[0])
        return deleted

    def prune_audio(self, *, now: datetime | None = None) -> int:
        current = _as_utc(now or datetime.now(UTC))
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """SELECT id, retained_audio_path FROM history
                WHERE retained_audio_path IS NOT NULL
                AND retained_audio_expires_at IS NOT NULL
                AND retained_audio_expires_at <= ?""",
                (current.isoformat(),),
            ).fetchall()
            with self._connection:
                self._connection.executemany(
                    """UPDATE history SET retained_audio_path = NULL,
                    retained_audio_expires_at = NULL WHERE id = ?""",
                    ((row["id"],) for row in rows),
                )
        for row in rows:
            self._delete_audio(row["retained_audio_path"])
        return len(rows)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> SQLiteHistoryStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _open_connection_with_recovery(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            self._initialize_schema(connection)
            return connection
        except sqlite3.DatabaseError as error:
            if connection is not None:
                connection.close()
            if not self._can_recover(error):
                raise
            self._recovery_backup = self._quarantine_corrupt_database()
            connection = self._open_connection()
            self._initialize_schema(connection)
            return connection

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        with connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    final_text TEXT NOT NULL,
                    language TEXT,
                    transcription_provider TEXT NOT NULL,
                    cleanup_provider TEXT,
                    duration_seconds REAL NOT NULL,
                    warning TEXT,
                    mode_id TEXT NOT NULL DEFAULT 'raw',
                    cleanup_model TEXT,
                    latency_ms INTEGER,
                    transform_name TEXT,
                    insertion_method TEXT,
                    retained_audio_path TEXT,
                    retained_audio_expires_at TEXT
                )
                """
            )
            existing = {
                row["name"] for row in connection.execute("PRAGMA table_info(history)").fetchall()
            }
            if not self._required_columns.issubset(existing):
                raise sqlite3.DatabaseError("incompatible OpenWhisper history schema")
            for column, column_type in self._optional_columns.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE history ADD COLUMN {column} {column_type}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS history_created_at ON history(created_at DESC)"
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS history_mode_provider
                ON history(mode_id, transcription_provider)"""
            )
            # Additive migration for pre-delivery history. Recover the old
            # single outcome from insertion_method while preserving all text.
            connection.execute(
                """UPDATE history SET inserted = 1
                WHERE inserted = 0 AND insertion_method IN ('atspi', 'x11', 'wayland')"""
            )
            connection.execute(
                """UPDATE history SET copied = 1
                WHERE copied = 0 AND insertion_method = 'clipboard'"""
            )
            self._initialize_fts(connection)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _initialize_fts(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
                raw_text, final_text, content='history', content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2')"""
            )
            connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS history_fts_insert AFTER INSERT ON history BEGIN
                  INSERT INTO history_fts(rowid, raw_text, final_text)
                  VALUES (new.rowid, new.raw_text, new.final_text);
                END;
                CREATE TRIGGER IF NOT EXISTS history_fts_delete AFTER DELETE ON history BEGIN
                  INSERT INTO history_fts(history_fts, rowid, raw_text, final_text)
                  VALUES ('delete', old.rowid, old.raw_text, old.final_text);
                END;
                CREATE TRIGGER IF NOT EXISTS history_fts_update AFTER UPDATE OF raw_text, final_text
                ON history BEGIN
                  INSERT INTO history_fts(history_fts, rowid, raw_text, final_text)
                  VALUES ('delete', old.rowid, old.raw_text, old.final_text);
                  INSERT INTO history_fts(rowid, raw_text, final_text)
                  VALUES (new.rowid, new.raw_text, new.final_text);
                END;
                """
            )
            connection.execute("INSERT INTO history_fts(history_fts) VALUES ('rebuild')")
            self._fts_available = True
        except sqlite3.OperationalError as error:
            if "fts5" not in str(error).casefold():
                raise
            self._fts_available = False

    def _prune_locked(self, now: datetime, days: int) -> int:
        cutoff = now - timedelta(days=days)
        cursor = self._connection.execute(
            "DELETE FROM history WHERE created_at < ?", (cutoff.isoformat(),)
        )
        return cursor.rowcount

    def _validated_audio_path(self, value: Path | None) -> Path | None:
        if value is None:
            return None
        path = Path(value).absolute()
        root = self.retained_audio_dir.absolute()
        if path.parent != root or not path.name.startswith("openwhisper-audio-"):
            raise ValueError("retained audio path is outside OpenWhisper storage")
        if path.is_symlink():
            raise ValueError("retained audio cannot be a symlink")
        return path

    def _delete_audio(self, value: str | Path | None) -> None:
        if not value:
            return
        try:
            path = self._validated_audio_path(Path(value))
        except ValueError:
            return
        if path is None:
            return
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        # Unlink the directory entry without following it. A changed owner is a
        # failed ownership check and must not leave sensitive captured audio.
        if path.is_symlink() or info.st_uid != os.getuid():
            path.unlink(missing_ok=True)
            return
        if path.is_file():
            path.unlink(missing_ok=True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("history store is closed")

    def _can_recover(self, error: sqlite3.DatabaseError) -> bool:
        if not self.path.is_file():
            return False
        message = str(error).casefold()
        return any(
            marker in message
            for marker in (
                "file is not a database",
                "malformed",
                "disk image is malformed",
                "unsupported file format",
                "incompatible openwhisper history schema",
            )
        )

    def _quarantine_corrupt_database(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}-{uuid.uuid4().hex[:8]}")
        os.replace(self.path, backup)
        for suffix in ("-wal", "-shm"):
            sidecar = self.path.with_name(f"{self.path.name}{suffix}")
            if sidecar.exists():
                os.replace(sidecar, backup.with_name(f"{backup.name}{suffix}"))
        return backup


def _record_values(record: HistoryRecord) -> tuple[object, ...]:
    return (
        record.id,
        _as_utc(record.created_at).isoformat() if record.created_at else None,
        record.raw_text,
        record.final_text,
        record.language,
        record.transcription_provider,
        record.cleanup_provider,
        record.duration_seconds,
        record.warning,
        record.mode_id,
        record.cleanup_model,
        record.latency_ms,
        record.transform_name,
        record.insertion_method,
        int(record.inserted),
        int(record.copied),
        str(record.retained_audio_path) if record.retained_audio_path else None,
        (
            _as_utc(record.retained_audio_expires_at).isoformat()
            if record.retained_audio_expires_at
            else None
        ),
    )


def _row_to_record(row: sqlite3.Row) -> HistoryRecord:
    keys = set(row.keys())

    def optional(name: str, default: object = None) -> object:
        return row[name] if name in keys else default

    audio_path = optional("retained_audio_path")
    audio_expiry = optional("retained_audio_expires_at")
    return HistoryRecord(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        raw_text=row["raw_text"],
        final_text=row["final_text"],
        language=row["language"],
        transcription_provider=row["transcription_provider"],
        cleanup_provider=optional("cleanup_provider"),
        duration_seconds=row["duration_seconds"],
        warning=optional("warning"),
        mode_id=str(optional("mode_id", "raw")),
        cleanup_model=optional("cleanup_model"),
        latency_ms=optional("latency_ms"),
        transform_name=optional("transform_name"),
        insertion_method=optional("insertion_method"),
        inserted=bool(
            optional(
                "inserted",
                str(optional("insertion_method", "")) in {"atspi", "x11", "wayland"},
            )
        ),
        copied=bool(
            optional("copied", str(optional("insertion_method", "")) == "clipboard")
        ),
        retained_audio_path=Path(str(audio_path)) if audio_path else None,
        retained_audio_expires_at=(
            datetime.fromisoformat(str(audio_expiry)) if audio_expiry else None
        ),
    )


def _fts_query(value: str) -> str:
    words = [word for word in value.strip().replace('"', " ").split() if word]
    return " AND ".join(f'"{word}"' for word in words)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("history timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
