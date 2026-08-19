use std::path::Path;
use std::sync::Mutex;

use chrono::{DateTime, Duration, Utc};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

use crate::state::Mode;

const SCHEMA_VERSION: i64 = 1;

#[derive(Debug, Error)]
pub enum StorageError {
    #[error("database error: {0}")]
    Database(#[from] rusqlite::Error),
    #[error("state database lock is poisoned")]
    Poisoned,
    #[error("history entry was not found")]
    NotFound,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct HistoryEntry {
    pub id: Uuid,
    pub created_at: DateTime<Utc>,
    pub raw_text: String,
    pub final_text: String,
    pub mode: Mode,
    pub language: String,
    pub duration_ms: u64,
    pub inserted: bool,
    pub source: String,
}

#[derive(Debug, Clone)]
pub struct HistoryInput {
    pub raw_text: String,
    pub final_text: String,
    pub mode: Mode,
    pub language: String,
    pub duration_ms: u64,
    pub inserted: bool,
    pub source: String,
}

pub struct StateStore {
    connection: Mutex<Connection>,
}

impl StateStore {
    pub fn open(path: &Path) -> Result<Self, StorageError> {
        let connection = Connection::open(path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).map_err(
                |error| {
                    StorageError::Database(rusqlite::Error::ToSqlConversionFailure(Box::new(error)))
                },
            )?;
        }
        Self::initialize(connection)
    }

    pub fn in_memory() -> Result<Self, StorageError> {
        Self::initialize(Connection::open_in_memory()?)
    }

    fn initialize(connection: Connection) -> Result<Self, StorageError> {
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.execute_batch(
            "BEGIN;
             CREATE TABLE IF NOT EXISTS schema_migrations (
                 version INTEGER PRIMARY KEY,
                 applied_at TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS history (
                 id TEXT PRIMARY KEY,
                 created_at TEXT NOT NULL,
                 raw_text TEXT NOT NULL,
                 final_text TEXT NOT NULL,
                 mode TEXT NOT NULL,
                 language TEXT NOT NULL,
                 duration_ms INTEGER NOT NULL,
                 inserted INTEGER NOT NULL,
                 source TEXT NOT NULL
             );
             CREATE INDEX IF NOT EXISTS history_created_at_idx ON history(created_at DESC);
             CREATE TABLE IF NOT EXISTS vocabulary (
                 term TEXT PRIMARY KEY,
                 added_at TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS replacements (
                 source TEXT PRIMARY KEY,
                 replacement TEXT NOT NULL,
                 added_at TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS snippets (
                 name TEXT PRIMARY KEY,
                 body TEXT NOT NULL,
                 added_at TEXT NOT NULL
             );
             INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                 VALUES (1, datetime('now'));
             PRAGMA user_version = 1;
             COMMIT;",
        )?;
        let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if version != SCHEMA_VERSION {
            return Err(StorageError::Database(rusqlite::Error::InvalidQuery));
        }
        Ok(Self {
            connection: Mutex::new(connection),
        })
    }

    fn connection(&self) -> Result<std::sync::MutexGuard<'_, Connection>, StorageError> {
        self.connection.lock().map_err(|_| StorageError::Poisoned)
    }

    pub fn add_history(&self, input: HistoryInput) -> Result<HistoryEntry, StorageError> {
        let entry = HistoryEntry {
            id: Uuid::new_v4(),
            created_at: Utc::now(),
            raw_text: input.raw_text,
            final_text: input.final_text,
            mode: input.mode,
            language: input.language,
            duration_ms: input.duration_ms,
            inserted: input.inserted,
            source: input.source,
        };
        self.connection()?.execute(
            "INSERT INTO history (id, created_at, raw_text, final_text, mode, language, duration_ms, inserted, source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                entry.id.to_string(), entry.created_at.to_rfc3339(), entry.raw_text,
                entry.final_text, mode_name(entry.mode), entry.language, entry.duration_ms,
                entry.inserted, entry.source,
            ],
        )?;
        Ok(entry)
    }

    pub fn list_history(&self, limit: usize) -> Result<Vec<HistoryEntry>, StorageError> {
        self.query_history(
            "SELECT id, created_at, raw_text, final_text, mode, language, duration_ms, inserted, source
             FROM history ORDER BY created_at DESC LIMIT ?1",
            params![limit as i64],
        )
    }

    pub fn search_history(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<HistoryEntry>, StorageError> {
        let escaped = query
            .replace('\\', "\\\\")
            .replace('%', "\\%")
            .replace('_', "\\_");
        self.query_history(
            "SELECT id, created_at, raw_text, final_text, mode, language, duration_ms, inserted, source
             FROM history WHERE raw_text LIKE ?1 ESCAPE '\\' OR final_text LIKE ?1 ESCAPE '\\'
             ORDER BY created_at DESC LIMIT ?2",
            params![format!("%{escaped}%"), limit as i64],
        )
    }

    pub fn show_history(&self, id: Uuid) -> Result<HistoryEntry, StorageError> {
        let connection = self.connection()?;
        connection
            .query_row(
                "SELECT id, created_at, raw_text, final_text, mode, language, duration_ms, inserted, source FROM history WHERE id = ?1",
                [id.to_string()], row_to_entry,
            )
            .optional()?
            .ok_or(StorageError::NotFound)
    }

    pub fn delete_history(&self, id: Uuid) -> Result<bool, StorageError> {
        Ok(self
            .connection()?
            .execute("DELETE FROM history WHERE id = ?1", [id.to_string()])?
            > 0)
    }

    pub fn clear_history(&self) -> Result<usize, StorageError> {
        Ok(self.connection()?.execute("DELETE FROM history", [])?)
    }

    pub fn prune_history(&self, retention_days: u16) -> Result<usize, StorageError> {
        if retention_days == 0 {
            return self.clear_history();
        }
        let cutoff = Utc::now() - Duration::days(i64::from(retention_days));
        Ok(self.connection()?.execute(
            "DELETE FROM history WHERE created_at < ?1",
            [cutoff.to_rfc3339()],
        )?)
    }

    fn query_history<P: rusqlite::Params>(
        &self,
        sql: &str,
        params: P,
    ) -> Result<Vec<HistoryEntry>, StorageError> {
        let connection = self.connection()?;
        let mut statement = connection.prepare(sql)?;
        let rows = statement.query_map(params, row_to_entry)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StorageError::from)
    }

    pub fn list_strings(&self, table: &str) -> Result<Vec<(String, Option<String>)>, StorageError> {
        let (sql, has_value) = match table {
            "vocabulary" => ("SELECT term, NULL FROM vocabulary ORDER BY term", false),
            "replacements" => (
                "SELECT source, replacement FROM replacements ORDER BY source",
                true,
            ),
            "snippets" => ("SELECT name, body FROM snippets ORDER BY name", true),
            _ => return Err(StorageError::Database(rusqlite::Error::InvalidQuery)),
        };
        let connection = self.connection()?;
        let mut statement = connection.prepare(sql)?;
        let rows = statement.query_map([], |row| {
            let value = if has_value { row.get(1)? } else { None };
            Ok((row.get(0)?, value))
        })?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(StorageError::from)
    }

    pub fn put_string(
        &self,
        table: &str,
        key: &str,
        value: Option<&str>,
    ) -> Result<(), StorageError> {
        let now = Utc::now().to_rfc3339();
        match table {
            "vocabulary" => self.connection()?.execute(
                "INSERT OR REPLACE INTO vocabulary(term, added_at) VALUES (?1, ?2)", params![key, now]
            )?,
            "replacements" => self.connection()?.execute(
                "INSERT OR REPLACE INTO replacements(source, replacement, added_at) VALUES (?1, ?2, ?3)",
                params![key, value.ok_or(StorageError::Database(rusqlite::Error::InvalidQuery))?, now]
            )?,
            "snippets" => self.connection()?.execute(
                "INSERT OR REPLACE INTO snippets(name, body, added_at) VALUES (?1, ?2, ?3)",
                params![key, value.ok_or(StorageError::Database(rusqlite::Error::InvalidQuery))?, now]
            )?,
            _ => return Err(StorageError::Database(rusqlite::Error::InvalidQuery)),
        };
        Ok(())
    }

    pub fn remove_string(&self, table: &str, key: &str) -> Result<bool, StorageError> {
        let column = match table {
            "vocabulary" => "term",
            "replacements" => "source",
            "snippets" => "name",
            _ => return Err(StorageError::Database(rusqlite::Error::InvalidQuery)),
        };
        let sql = format!("DELETE FROM {table} WHERE {column} = ?1");
        Ok(self.connection()?.execute(&sql, [key])? > 0)
    }
}

fn mode_name(mode: Mode) -> &'static str {
    match mode {
        Mode::Raw => "raw",
        Mode::Clean => "clean",
        Mode::Code => "code",
    }
}

fn row_to_entry(row: &rusqlite::Row<'_>) -> rusqlite::Result<HistoryEntry> {
    let id: String = row.get(0)?;
    let created_at: String = row.get(1)?;
    let mode: String = row.get(4)?;
    Ok(HistoryEntry {
        id: Uuid::parse_str(&id).map_err(|e| {
            rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Text, Box::new(e))
        })?,
        created_at: DateTime::parse_from_rfc3339(&created_at)
            .map_err(|e| {
                rusqlite::Error::FromSqlConversionFailure(
                    1,
                    rusqlite::types::Type::Text,
                    Box::new(e),
                )
            })?
            .with_timezone(&Utc),
        raw_text: row.get(2)?,
        final_text: row.get(3)?,
        mode: match mode.as_str() {
            "raw" => Mode::Raw,
            "clean" => Mode::Clean,
            "code" => Mode::Code,
            _ => return Err(rusqlite::Error::InvalidQuery),
        },
        language: row.get(5)?,
        duration_ms: row.get(6)?,
        inserted: row.get(7)?,
        source: row.get(8)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(text: &str) -> HistoryInput {
        HistoryInput {
            raw_text: text.into(),
            final_text: text.into(),
            mode: Mode::Raw,
            language: "ar-en".into(),
            duration_ms: 500,
            inserted: false,
            source: "microphone".into(),
        }
    }

    #[test]
    fn stores_and_searches_logical_unicode() {
        let store = StateStore::in_memory().unwrap();
        let entry = store.add_history(input("شغّل cargo test")).unwrap();
        assert_eq!(store.search_history("cargo", 10).unwrap(), vec![entry]);
    }

    #[test]
    fn zero_retention_clears_history() {
        let store = StateStore::in_memory().unwrap();
        store.add_history(input("temporary")).unwrap();
        assert_eq!(store.prune_history(0).unwrap(), 1);
        assert!(store.list_history(10).unwrap().is_empty());
    }

    #[test]
    fn supports_vocabulary_replacements_and_snippets() {
        let store = StateStore::in_memory().unwrap();
        store.put_string("vocabulary", "OpenWhisper", None).unwrap();
        store
            .put_string("replacements", "open whisper", Some("OpenWhisper"))
            .unwrap();
        store
            .put_string("snippets", "sig", Some("Best regards"))
            .unwrap();
        assert_eq!(store.list_strings("vocabulary").unwrap().len(), 1);
        assert_eq!(
            store.list_strings("replacements").unwrap()[0].1.as_deref(),
            Some("OpenWhisper")
        );
        assert_eq!(store.list_strings("snippets").unwrap()[0].0, "sig");
    }
}
