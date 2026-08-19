use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use directories::{BaseDirs, ProjectDirs};
use serde::{Deserialize, Serialize};
use thiserror::Error;

const APP_DIR: &str = "openwhisper-v1";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppPaths {
    pub config_dir: PathBuf,
    pub data_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub runtime_dir: PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LegacyDataReport {
    pub detected: bool,
    pub paths: Vec<PathBuf>,
    pub message: String,
}

#[derive(Debug, Error)]
pub enum PathError {
    #[error("could not determine per-user application directories")]
    Unavailable,
    #[error("could not create {path}: {source}")]
    Create {
        path: PathBuf,
        source: std::io::Error,
    },
}

impl AppPaths {
    pub fn discover() -> Result<Self, PathError> {
        if let Some(root) = env::var_os("OPENWHISPER_V1_HOME") {
            return Ok(Self::under(PathBuf::from(root)));
        }
        let project = ProjectDirs::from("io.github.yousufaltayeb", "OpenWhisper", "OpenWhisper")
            .ok_or(PathError::Unavailable)?;
        let runtime_dir = env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| project.data_local_dir().to_path_buf())
            .join(APP_DIR);
        Ok(Self {
            config_dir: project.config_dir().join("v1"),
            data_dir: project.data_dir().join("v1"),
            cache_dir: project.cache_dir().join("v1"),
            runtime_dir,
        })
    }

    pub fn under(root: impl Into<PathBuf>) -> Self {
        let root = root.into();
        Self {
            config_dir: root.join("config"),
            data_dir: root.join("data"),
            cache_dir: root.join("cache"),
            runtime_dir: root.join("run"),
        }
    }

    pub fn ensure(&self) -> Result<(), PathError> {
        for path in [
            &self.config_dir,
            &self.data_dir,
            &self.cache_dir,
            &self.runtime_dir,
        ] {
            fs::create_dir_all(path).map_err(|source| PathError::Create {
                path: path.clone(),
                source,
            })?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(|source| {
                    PathError::Create {
                        path: path.clone(),
                        source,
                    }
                })?;
            }
        }
        Ok(())
    }

    pub fn config_file(&self) -> PathBuf {
        self.config_dir.join("config.toml")
    }

    pub fn state_file(&self) -> PathBuf {
        self.data_dir.join("state.sqlite3")
    }

    pub fn lock_file(&self) -> PathBuf {
        self.runtime_dir.join("openwhisperd.lock")
    }

    #[cfg(unix)]
    pub fn socket_file(&self) -> PathBuf {
        self.runtime_dir.join("openwhisperd.sock")
    }

    pub fn detect_legacy(&self) -> LegacyDataReport {
        let mut candidates = Vec::new();
        if let Some(base) = BaseDirs::new() {
            candidates.extend([
                base.config_dir().join("whisper/config.ini"),
                base.config_dir().join("openwhisper/config.ini"),
                base.data_dir().join("openwhisper/history.json"),
                base.data_dir().join("openwhisper/history.sqlite3"),
                base.home_dir()
                    .join(".cache/huggingface/hub/models--Systran--faster-whisper-large-v3"),
            ]);
        }
        Self::legacy_report_from(candidates)
    }

    pub fn legacy_report_from(paths: impl IntoIterator<Item = PathBuf>) -> LegacyDataReport {
        let paths: Vec<_> = paths.into_iter().filter(|path| path.exists()).collect();
        let detected = !paths.is_empty();
        LegacyDataReport {
            detected,
            paths,
            message: if detected {
                "Legacy OpenWhisper data was detected and remains untouched. OpenWhisper 1.0 uses a new config.toml and state.sqlite3.".into()
            } else {
                "No legacy OpenWhisper data was detected.".into()
            },
        }
    }

    pub fn is_v1_path(&self, path: &Path) -> bool {
        path.starts_with(&self.config_dir)
            || path.starts_with(&self.data_dir)
            || path.starts_with(&self.cache_dir)
            || path.starts_with(&self.runtime_dir)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn v1_paths_are_isolated_and_versioned() {
        let paths = AppPaths::under("/tmp/openwhisper-test");
        assert!(paths.config_file().ends_with("config/config.toml"));
        assert!(paths.state_file().ends_with("data/state.sqlite3"));
        assert_ne!(paths.config_file(), PathBuf::from("config.ini"));
    }

    #[cfg(unix)]
    #[test]
    fn v1_directories_are_private() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let paths = AppPaths::under(temp.path());
        paths.ensure().unwrap();
        assert_eq!(
            std::fs::metadata(&paths.data_dir)
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }

    #[test]
    fn legacy_detection_only_checks_metadata() {
        let temp = tempfile::tempdir().unwrap();
        let legacy = temp.path().join("config.ini");
        std::fs::write(&legacy, "SECRET=must-not-be-read").unwrap();
        let report = AppPaths::legacy_report_from([legacy.clone()]);
        assert!(report.detected);
        assert_eq!(report.paths, vec![legacy]);
        assert!(!report.message.contains("SECRET"));
    }
}
