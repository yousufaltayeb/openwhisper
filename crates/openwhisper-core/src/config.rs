use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::paths::AppPaths;
use crate::state::Mode;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct AppConfig {
    pub schema_version: u16,
    pub mode: Mode,
    pub language: String,
    pub overlay: OverlayMode,
    pub sounds: SoundConfig,
    pub notifications: bool,
    pub history: HistoryConfig,
    pub privacy: PrivacyConfig,
    pub provider: ProviderConfig,
    pub audio: AudioConfig,
    pub model: ModelConfig,
    pub delivery: DeliveryConfig,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AudioBackend {
    Auto,
    Pipewire,
    Pulse,
    Alsa,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct AudioConfig {
    pub backend: AudioBackend,
    pub device: String,
    pub max_recording_seconds: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct ModelConfig {
    pub selected: String,
    pub threads: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct DeliveryConfig {
    pub clipboard: bool,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum OverlayMode {
    Auto,
    Always,
    Never,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct SoundConfig {
    pub start: bool,
    pub stop: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct HistoryConfig {
    pub enabled: bool,
    pub retention_days: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct PrivacyConfig {
    pub local_only: bool,
    pub encrypted_secret_fallback: bool,
    pub transcript_logs: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, deny_unknown_fields)]
pub struct ProviderConfig {
    pub transcription: String,
    pub cleanup: String,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            schema_version: 2,
            mode: Mode::Raw,
            language: "auto".into(),
            overlay: OverlayMode::Auto,
            sounds: SoundConfig::default(),
            notifications: true,
            history: HistoryConfig::default(),
            privacy: PrivacyConfig::default(),
            provider: ProviderConfig::default(),
            audio: AudioConfig::default(),
            model: ModelConfig::default(),
            delivery: DeliveryConfig::default(),
        }
    }
}

impl Default for AudioConfig {
    fn default() -> Self {
        Self {
            backend: AudioBackend::Auto,
            device: String::new(),
            max_recording_seconds: 300,
        }
    }
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            selected: "balanced".into(),
            threads: 0,
        }
    }
}

impl Default for DeliveryConfig {
    fn default() -> Self {
        Self { clipboard: true }
    }
}

impl Default for SoundConfig {
    fn default() -> Self {
        Self {
            start: true,
            stop: true,
        }
    }
}

impl Default for HistoryConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            retention_days: 30,
        }
    }
}

impl Default for PrivacyConfig {
    fn default() -> Self {
        Self {
            local_only: true,
            encrypted_secret_fallback: false,
            transcript_logs: false,
        }
    }
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self {
            transcription: "local".into(),
            cleanup: "deterministic".into(),
        }
    }
}

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("could not read configuration at {path}: {source}")]
    Read {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("configuration at {path} is invalid: {source}")]
    Parse {
        path: PathBuf,
        source: toml::de::Error,
    },
    #[error("configuration is invalid: {0}")]
    Validation(String),
    #[error("could not write configuration at {path}: {source}")]
    Write {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("could not encode configuration: {0}")]
    Encode(#[from] toml::ser::Error),
}

impl AppConfig {
    pub fn load_or_create(paths: &AppPaths) -> Result<Self, ConfigError> {
        let path = paths.config_file();
        if !path.exists() {
            let config = Self::default();
            config.save(&path)?;
            return Ok(config);
        }
        let raw = fs::read_to_string(&path).map_err(|source| ConfigError::Read {
            path: path.clone(),
            source,
        })?;
        let mut document: toml::Value =
            toml::from_str(&raw).map_err(|source| ConfigError::Parse {
                path: path.clone(),
                source,
            })?;
        let version = document
            .get("schema_version")
            .and_then(toml::Value::as_integer)
            .unwrap_or(1);
        if version == 1 {
            let table = document.as_table_mut().ok_or_else(|| {
                ConfigError::Validation("configuration root must be a table".into())
            })?;
            table.insert("schema_version".into(), toml::Value::Integer(2));
            table.insert(
                "audio".into(),
                toml::Value::try_from(AudioConfig::default()).map_err(ConfigError::Encode)?,
            );
            table.insert(
                "model".into(),
                toml::Value::try_from(ModelConfig::default()).map_err(ConfigError::Encode)?,
            );
            table.insert(
                "delivery".into(),
                toml::Value::try_from(DeliveryConfig::default()).map_err(ConfigError::Encode)?,
            );
        }
        let config: Self = document.try_into().map_err(|source| ConfigError::Parse {
            path: path.clone(),
            source,
        })?;
        config.validate()?;
        if version == 1 {
            config.save(&path)?;
        }
        Ok(config)
    }

    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.schema_version != 2 {
            return Err(ConfigError::Validation(format!(
                "unsupported config schema version {}",
                self.schema_version
            )));
        }
        if self.history.retention_days > 3650 {
            return Err(ConfigError::Validation(
                "history retention exceeds 3650 days".into(),
            ));
        }
        if self.privacy.transcript_logs {
            return Err(ConfigError::Validation(
                "transcript-bearing logs are prohibited".into(),
            ));
        }
        if !(10..=600).contains(&self.audio.max_recording_seconds) {
            return Err(ConfigError::Validation(
                "audio.max_recording_seconds must be between 10 and 600".into(),
            ));
        }
        if !matches!(self.language.as_str(), "auto" | "ar" | "en") {
            return Err(ConfigError::Validation(
                "language must be auto, ar, or en".into(),
            ));
        }
        Ok(())
    }

    pub fn save(&self, path: &Path) -> Result<(), ConfigError> {
        self.validate()?;
        let parent = path.parent().ok_or_else(|| {
            ConfigError::Validation("configuration path has no parent directory".into())
        })?;
        fs::create_dir_all(parent).map_err(|source| ConfigError::Write {
            path: parent.to_path_buf(),
            source,
        })?;
        let temporary = path.with_extension("toml.tmp");
        fs::write(&temporary, toml::to_string_pretty(self)?).map_err(|source| {
            ConfigError::Write {
                path: temporary.clone(),
                source,
            }
        })?;
        fs::rename(&temporary, path).map_err(|source| ConfigError::Write {
            path: path.to_path_buf(),
            source,
        })?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(0o600)).map_err(|source| {
                ConfigError::Write {
                    path: path.to_path_buf(),
                    source,
                }
            })?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_enforce_privacy_and_thirty_day_retention() {
        let config = AppConfig::default();
        assert_eq!(config.history.retention_days, 30);
        assert!(config.privacy.local_only);
        assert!(!config.privacy.transcript_logs);
        assert_eq!(config.overlay, OverlayMode::Auto);
        assert!(config.sounds.start && config.sounds.stop && config.notifications);
        assert_eq!(config.schema_version, 2);
        assert_eq!(config.audio.max_recording_seconds, 300);
        assert_eq!(config.model.selected, "balanced");
        assert!(config.delivery.clipboard);
    }

    #[test]
    fn load_does_not_consult_legacy_ini() {
        let temp = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(temp.path());
        paths.ensure().unwrap();
        std::fs::write(temp.path().join("config.ini"), "mode=clean").unwrap();
        let config = AppConfig::load_or_create(&paths).unwrap();
        assert_eq!(config.mode, Mode::Raw);
    }

    #[test]
    fn migrates_v1_atomically_to_v2() {
        let temp = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(temp.path());
        paths.ensure().unwrap();
        let mut value = toml::Value::try_from(AppConfig::default()).unwrap();
        let table = value.as_table_mut().unwrap();
        table.insert("schema_version".into(), toml::Value::Integer(1));
        table.remove("audio");
        table.remove("model");
        table.remove("delivery");
        std::fs::write(paths.config_file(), toml::to_string_pretty(&value).unwrap()).unwrap();
        let config = AppConfig::load_or_create(&paths).unwrap();
        assert_eq!(config.schema_version, 2);
        let persisted = std::fs::read_to_string(paths.config_file()).unwrap();
        assert!(persisted.contains("[audio]"));
    }
}
