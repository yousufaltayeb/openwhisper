use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ModelArtifact {
    pub id: String,
    pub engine: String,
    pub license: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub runtime_abi: String,
}

#[derive(Debug, Error)]
pub enum ModelError {
    #[error("model catalog entry is not release-pinned")]
    Unpinned,
    #[error("model file size differs from the signed catalog")]
    SizeMismatch,
    #[error("model SHA-256 differs from the signed catalog")]
    HashMismatch { quarantine: PathBuf },
    #[error("model I/O failed: {0}")]
    Io(#[from] io::Error),
}

impl ModelArtifact {
    pub fn validate(&self) -> Result<(), ModelError> {
        if self.sha256.len() != 64 || !self.sha256.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(ModelError::Unpinned);
        }
        Ok(())
    }

    pub fn import_offline(&self, source: &Path, model_dir: &Path) -> Result<PathBuf, ModelError> {
        self.validate()?;
        fs::create_dir_all(model_dir)?;
        if fs::metadata(source)?.len() != self.size_bytes {
            return Err(ModelError::SizeMismatch);
        }
        let staging = model_dir.join(format!(".{}.{}.partial", self.id, Uuid::new_v4()));
        fs::copy(source, &staging)?;
        let actual = sha256_file(&staging)?;
        if actual != self.sha256.to_ascii_lowercase() {
            let quarantine = model_dir.join(format!("{}.{}.corrupt", self.id, Uuid::new_v4()));
            fs::rename(&staging, &quarantine)?;
            return Err(ModelError::HashMismatch { quarantine });
        }
        let destination = model_dir.join(format!("{}.bin", self.id));
        fs::rename(&staging, &destination)?;
        Ok(destination)
    }
}

fn sha256_file(path: &Path) -> Result<String, io::Error> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offline_import_is_verified_and_atomic() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("model.download");
        fs::write(&source, b"verified model bytes").unwrap();
        let spec = ModelArtifact {
            id: "fixture".into(),
            engine: "whisper.cpp".into(),
            license: "test-only".into(),
            size_bytes: 20,
            sha256: sha256_file(&source).unwrap(),
            runtime_abi: "fixture-1".into(),
        };
        let installed = spec
            .import_offline(&source, &temp.path().join("models"))
            .unwrap();
        assert_eq!(fs::read(installed).unwrap(), b"verified model bytes");
        assert!(temp.path().join("models").read_dir().unwrap().all(|item| {
            !item
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with("partial")
        }));
    }

    #[test]
    fn corrupt_import_is_quarantined() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("bad");
        fs::write(&source, b"bad").unwrap();
        let spec = ModelArtifact {
            id: "fixture".into(),
            engine: "whisper.cpp".into(),
            license: "test-only".into(),
            size_bytes: 3,
            sha256: "0".repeat(64),
            runtime_abi: "fixture-1".into(),
        };
        let ModelError::HashMismatch { quarantine } = spec
            .import_offline(&source, &temp.path().join("models"))
            .unwrap_err()
        else {
            panic!("expected hash mismatch")
        };
        assert!(quarantine.exists());
    }
}
