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
            schema_version: 1,
            mode: Mode::Raw,
            language: "auto".into(),
            overlay: OverlayMode::Auto,
            sounds: SoundConfig::default(),
            notifications: true,
            history: HistoryConfig::default(),
            privacy: PrivacyConfig::default(),
            provider: ProviderConfig::default(),
        }
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
        let config: Self = toml::from_str(&raw).map_err(|source| ConfigError::Parse {
            path: path.clone(),
            source,
        })?;
        config.validate()?;
        Ok(config)
    }

    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.schema_version != 1 {
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
}
