use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use reqwest::StatusCode;
use reqwest::header::{CONTENT_LENGTH, CONTENT_RANGE, ETAG, IF_RANGE, RANGE};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use uuid::Uuid;

pub const BUILTIN_MODEL_NAME: &str = "balanced";
pub const BUILTIN_MODEL_ID: &str = "large-v3-turbo-q5_0";
pub const BUILTIN_MODEL_SOURCE: &str = "https://huggingface.co/ggerganov/whisper.cpp/resolve/98aa99a0a9db05ae2342309f5096248665f7cba3/ggml-large-v3-turbo-q5_0.bin";
pub const BUILTIN_MODEL_SIZE: u64 = 574_041_195;
pub const BUILTIN_MODEL_SHA256: &str =
    "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2";
pub const BUILTIN_MODEL_LICENSE: &str = "MIT";
pub const BUILTIN_WORKER_ABI: &str = "openwhisper-worker-1";
pub const DOWNLOAD_DISK_MARGIN: u64 = 64 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BuiltinModelManifest {
    pub name: String,
    pub model_id: String,
    pub artifact_name: String,
    pub pinned_revision: String,
    pub source: String,
    pub license: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub worker_abi: String,
    pub trust: String,
    pub benchmark_status: String,
}

fn manifest(
    name: &str,
    model_id: &str,
    artifact_name: &str,
    revision: &str,
    size_bytes: u64,
    sha256: &str,
) -> BuiltinModelManifest {
    BuiltinModelManifest {
        name: name.into(),
        model_id: model_id.into(),
        artifact_name: artifact_name.into(),
        pinned_revision: revision.into(),
        source: format!(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/{revision}/{artifact_name}"
        ),
        license: BUILTIN_MODEL_LICENSE.into(),
        size_bytes,
        sha256: sha256.into(),
        worker_abi: BUILTIN_WORKER_ABI.into(),
        trust: "builtin_pinned".into(),
        benchmark_status: "not_run".into(),
    }
}

pub fn builtin_fast_model() -> BuiltinModelManifest {
    manifest(
        "fast",
        "small-q5_1",
        "ggml-small-q5_1.bin",
        "98aa99a0a9db05ae2342309f5096248665f7cba3",
        190_085_487,
        "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
    )
}

pub fn builtin_balanced_model() -> BuiltinModelManifest {
    manifest(
        BUILTIN_MODEL_NAME,
        BUILTIN_MODEL_ID,
        "ggml-large-v3-turbo-q5_0.bin",
        "98aa99a0a9db05ae2342309f5096248665f7cba3",
        BUILTIN_MODEL_SIZE,
        BUILTIN_MODEL_SHA256,
    )
}

pub fn builtin_accurate_model() -> BuiltinModelManifest {
    manifest(
        "accurate",
        "large-v3-q5_0",
        "ggml-large-v3-q5_0.bin",
        "c521a4b02f422512d734391fdf08bb08c0862f68",
        1_081_140_203,
        "d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1",
    )
}

pub fn builtin_models() -> [BuiltinModelManifest; 3] {
    [
        builtin_fast_model(),
        builtin_balanced_model(),
        builtin_accurate_model(),
    ]
}

pub fn builtin_model(name: &str) -> Option<BuiltinModelManifest> {
    builtin_models()
        .into_iter()
        .find(|model| model.name == name)
}

impl BuiltinModelManifest {
    pub fn artifact(&self) -> ModelArtifact {
        ModelArtifact {
            id: self.model_id.clone(),
            engine: "whisper.cpp".into(),
            license: self.license.clone(),
            size_bytes: self.size_bytes,
            sha256: self.sha256.clone(),
            runtime_abi: self.worker_abi.clone(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ModelArtifact {
    pub id: String,
    pub engine: String,
    pub license: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub runtime_abi: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SignedCatalog {
    pub schema_version: u16,
    pub sequence: u64,
    pub expires_at: DateTime<Utc>,
    pub models: Vec<CatalogModel>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CatalogModel {
    pub name: String,
    pub model_id: String,
    pub source_url: String,
    pub license: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub worker_abi: String,
    pub supported_targets: Vec<String>,
    pub benchmark_result_digest: String,
    pub benchmark_approved: bool,
}

#[derive(Debug, Error)]
pub enum ModelError {
    #[error("model catalog entry is not release-pinned")]
    Unpinned,
    #[error("model file size differs from the signed catalog")]
    SizeMismatch,
    #[error("model SHA-256 differs from the signed catalog")]
    HashMismatch { quarantine: PathBuf },
    #[error("model ABI differs from this worker")]
    AbiMismatch,
    #[error("model license is missing")]
    LicenseMissing,
    #[error("catalog signature is invalid")]
    InvalidSignature,
    #[error("catalog is expired; existing verified models remain usable")]
    CatalogExpired,
    #[error("catalog sequence would roll back trusted metadata")]
    CatalogRollback,
    #[error("catalog does not support this target")]
    UnsupportedTarget,
    #[error("catalog schema is unsupported")]
    CatalogSchema,
    #[error("model source URL must use HTTPS")]
    InsecureSource,
    #[error("model download was not explicitly confirmed")]
    ConfirmationRequired,
    #[error("another model operation is already in progress")]
    InstallConflict,
    #[error("model download failed: {0}")]
    Network(String),
    #[error("model server returned HTTP {0}")]
    HttpStatus(u16),
    #[error("model server returned an invalid resume response")]
    InvalidResume,
    #[error("model server returned more than the pinned size")]
    DownloadOverflow,
    #[error("not enough free disk space: {available} bytes available, {required} required")]
    InsufficientDisk { available: u64, required: u64 },
    #[error("model path is outside OpenWhisper's canonical model directory")]
    UnsafePath,
    #[error("catalog JSON is invalid: {0}")]
    CatalogJson(#[from] serde_json::Error),
    #[error("model I/O failed: {0}")]
    Io(#[from] io::Error),
}

impl ModelArtifact {
    pub fn validate(&self) -> Result<(), ModelError> {
        if self.license.trim().is_empty() {
            return Err(ModelError::LicenseMissing);
        }
        if self.sha256.len() != 64 || !self.sha256.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(ModelError::Unpinned);
        }
        Ok(())
    }

    pub fn validate_for_abi(&self, worker_abi: &str) -> Result<(), ModelError> {
        self.validate()?;
        if self.runtime_abi != worker_abi {
            return Err(ModelError::AbiMismatch);
        }
        Ok(())
    }

    pub fn canonical_path(&self, model_dir: &Path) -> PathBuf {
        model_dir.join(format!("{}.bin", self.id))
    }

    pub fn import_offline(&self, source: &Path, model_dir: &Path) -> Result<PathBuf, ModelError> {
        self.validate()?;
        fs::create_dir_all(model_dir)?;
        let destination = self.canonical_path(model_dir);
        if source == destination && verify_file(self, source).is_ok() {
            set_private_permissions(source)?;
            return Ok(destination);
        }
        if fs::metadata(source)?.len() != self.size_bytes {
            return Err(ModelError::SizeMismatch);
        }
        let staging = model_dir.join(format!(".{}.{}.partial", self.id, Uuid::new_v4()));
        let mut input = fs::File::open(source)?;
        let mut output = private_file(&staging, true)?;
        io::copy(&mut input, &mut output)?;
        output.sync_all()?;
        let staging_file = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(&staging)?;
        staging_file.sync_all()?;
        let actual = sha256_file(&staging)?;
        if actual != self.sha256.to_ascii_lowercase() {
            let quarantine = model_dir.join(format!("{}.{}.corrupt", self.id, Uuid::new_v4()));
            fs::rename(&staging, &quarantine)?;
            sync_directory(model_dir)?;
            return Err(ModelError::HashMismatch { quarantine });
        }
        if destination.exists() {
            quarantine_file(&destination, model_dir, &self.id)?;
        }
        fs::rename(&staging, &destination)?;
        set_private_permissions(&destination)?;
        sync_directory(model_dir)?;
        Ok(destination)
    }
}

#[derive(Debug, Clone, Copy)]
pub struct DownloadOptions {
    pub allow_http: bool,
    pub disk_margin_bytes: u64,
}

impl Default for DownloadOptions {
    fn default() -> Self {
        Self {
            allow_http: false,
            disk_margin_bytes: DOWNLOAD_DISK_MARGIN,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DownloadOutcome {
    pub path: PathBuf,
    pub reused: bool,
    pub downloaded_bytes: u64,
}

pub async fn download_model(
    artifact: &ModelArtifact,
    source_url: &str,
    model_dir: &Path,
    options: DownloadOptions,
    mut progress: impl FnMut(u64, u64) + Send,
) -> Result<DownloadOutcome, ModelError> {
    artifact.validate()?;
    let source =
        reqwest::Url::parse(source_url).map_err(|error| ModelError::Network(error.to_string()))?;
    if source.scheme() != "https" && !(options.allow_http && source.scheme() == "http") {
        return Err(ModelError::InsecureSource);
    }
    fs::create_dir_all(model_dir)?;
    let destination = artifact.canonical_path(model_dir);
    if destination.exists() {
        match verify_file(artifact, &destination) {
            Ok(()) => {
                set_private_permissions(&destination)?;
                progress(artifact.size_bytes, artifact.size_bytes);
                return Ok(DownloadOutcome {
                    path: destination,
                    reused: true,
                    downloaded_bytes: 0,
                });
            }
            Err(_) => {
                quarantine_file(&destination, model_dir, &artifact.id)?;
            }
        }
    }

    let partial = model_dir.join(format!(".{}.partial", artifact.id));
    let etag_path = model_dir.join(format!(".{}.etag", artifact.id));
    if partial.exists() && fs::metadata(&partial)?.len() > artifact.size_bytes {
        quarantine_file(&partial, model_dir, &artifact.id)?;
        let _ = fs::remove_file(&etag_path);
    }

    let allow_http = options.allow_http;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::custom(move |attempt| {
            let scheme = attempt.url().scheme();
            if scheme != "https" && !(allow_http && scheme == "http") {
                attempt.stop()
            } else if attempt.previous().len() >= 10 {
                attempt.error("too many model download redirects")
            } else {
                attempt.follow()
            }
        }))
        .build()
        .map_err(|error| ModelError::Network(error.to_string()))?;

    let mut restarted_for_etag = false;
    loop {
        let mut offset = partial
            .metadata()
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        let stored_etag = fs::read_to_string(&etag_path)
            .ok()
            .filter(|value| !value.trim().is_empty());
        if offset > 0 && stored_etag.is_none() {
            let _ = fs::remove_file(&partial);
            offset = 0;
        }
        preflight_disk_space(
            model_dir,
            artifact.size_bytes.saturating_sub(offset),
            options.disk_margin_bytes,
        )?;

        let mut request = client.get(source.clone());
        if offset > 0 {
            request = request
                .header(RANGE, format!("bytes={offset}-"))
                .header(IF_RANGE, stored_etag.as_deref().unwrap_or_default());
        }
        let response = request
            .send()
            .await
            .map_err(|error| ModelError::Network(error.to_string()))?;
        let status = response.status();
        if !status.is_success() {
            return Err(ModelError::HttpStatus(status.as_u16()));
        }
        let response_etag = response
            .headers()
            .get(ETAG)
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);

        let append = if offset > 0 && status == StatusCode::PARTIAL_CONTENT {
            let range_ok = response
                .headers()
                .get(CONTENT_RANGE)
                .and_then(|value| value.to_str().ok())
                .is_some_and(|value| value.starts_with(&format!("bytes {offset}-")));
            if !range_ok {
                return Err(ModelError::InvalidResume);
            }
            if response_etag.as_deref() != stored_etag.as_deref() {
                if restarted_for_etag {
                    return Err(ModelError::InvalidResume);
                }
                restarted_for_etag = true;
                drop(response);
                let _ = fs::remove_file(&partial);
                let _ = fs::remove_file(&etag_path);
                continue;
            }
            true
        } else if offset > 0 && status == StatusCode::OK {
            offset = 0;
            false
        } else if offset == 0 && matches!(status, StatusCode::OK | StatusCode::PARTIAL_CONTENT) {
            false
        } else {
            return Err(ModelError::InvalidResume);
        };

        let remaining = artifact.size_bytes.saturating_sub(offset);
        if response
            .headers()
            .get(CONTENT_LENGTH)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse::<u64>().ok())
            .is_some_and(|length| length > remaining)
        {
            return Err(ModelError::DownloadOverflow);
        }
        if let Some(etag) = response_etag.as_deref() {
            write_private_metadata(&etag_path, etag.as_bytes())?;
        }
        let mut output = private_file(&partial, !append)?;
        if append {
            output = fs::OpenOptions::new()
                .append(true)
                .read(true)
                .open(&partial)?;
        }
        let mut downloaded = offset;
        progress(downloaded, artifact.size_bytes);
        let mut body = response;
        while let Some(chunk) = body
            .chunk()
            .await
            .map_err(|error| ModelError::Network(error.to_string()))?
        {
            let next = downloaded.saturating_add(chunk.len() as u64);
            if next > artifact.size_bytes {
                output.sync_all()?;
                quarantine_file(&partial, model_dir, &artifact.id)?;
                let _ = fs::remove_file(&etag_path);
                return Err(ModelError::DownloadOverflow);
            }
            output.write_all(&chunk)?;
            downloaded = next;
            progress(downloaded, artifact.size_bytes);
        }
        output.sync_all()?;
        if downloaded != artifact.size_bytes {
            return Err(ModelError::SizeMismatch);
        }
        let actual = sha256_file(&partial)?;
        if actual != artifact.sha256.to_ascii_lowercase() {
            let quarantine = quarantine_file(&partial, model_dir, &artifact.id)?;
            let _ = fs::remove_file(&etag_path);
            return Err(ModelError::HashMismatch { quarantine });
        }
        if destination.exists() {
            quarantine_file(&destination, model_dir, &artifact.id)?;
        }
        fs::rename(&partial, &destination)?;
        set_private_permissions(&destination)?;
        let _ = fs::remove_file(&etag_path);
        sync_directory(model_dir)?;
        return Ok(DownloadOutcome {
            path: destination,
            reused: false,
            downloaded_bytes: downloaded.saturating_sub(offset),
        });
    }
}

pub fn verify_file(artifact: &ModelArtifact, path: &Path) -> Result<(), ModelError> {
    artifact.validate()?;
    if fs::metadata(path)?.len() != artifact.size_bytes {
        return Err(ModelError::SizeMismatch);
    }
    if sha256_file(path)? != artifact.sha256.to_ascii_lowercase() {
        return Err(ModelError::HashMismatch {
            quarantine: path.to_path_buf(),
        });
    }
    Ok(())
}

pub fn quarantine_file(path: &Path, model_dir: &Path, id: &str) -> Result<PathBuf, ModelError> {
    if path.parent() != Some(model_dir) {
        return Err(ModelError::UnsafePath);
    }
    let quarantine = model_dir.join(format!("{id}.{}.corrupt", Uuid::new_v4()));
    fs::rename(path, &quarantine)?;
    set_private_permissions(&quarantine)?;
    sync_directory(model_dir)?;
    Ok(quarantine)
}

fn preflight_disk_space(model_dir: &Path, remaining: u64, margin: u64) -> Result<(), ModelError> {
    let available = fs2::available_space(model_dir)?;
    let required = remaining.saturating_add(margin);
    if available < required {
        return Err(ModelError::InsufficientDisk {
            available,
            required,
        });
    }
    Ok(())
}

fn private_file(path: &Path, truncate: bool) -> Result<fs::File, io::Error> {
    let file = fs::OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .truncate(truncate)
        .open(path)?;
    set_private_permissions(path)?;
    Ok(file)
}

fn write_private_metadata(path: &Path, bytes: &[u8]) -> Result<(), io::Error> {
    let mut file = private_file(path, true)?;
    file.write_all(bytes)?;
    file.sync_all()
}

fn set_private_permissions(path: &Path) -> Result<(), io::Error> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

fn sync_directory(path: &Path) -> Result<(), io::Error> {
    fs::File::open(path)?.sync_all()
}

impl SignedCatalog {
    pub fn verify_exact_bytes(
        bytes: &[u8],
        signature_bytes: &[u8; 64],
        public_key: &[u8; 32],
        now: DateTime<Utc>,
        highest_sequence: u64,
        target: &str,
        worker_abi: &str,
    ) -> Result<Self, ModelError> {
        let key = VerifyingKey::from_bytes(public_key).map_err(|_| ModelError::InvalidSignature)?;
        key.verify(bytes, &Signature::from_bytes(signature_bytes))
            .map_err(|_| ModelError::InvalidSignature)?;
        let catalog: Self = serde_json::from_slice(bytes)?;
        if catalog.schema_version != 1 {
            return Err(ModelError::CatalogSchema);
        }
        if catalog.sequence < highest_sequence {
            return Err(ModelError::CatalogRollback);
        }
        if catalog.expires_at <= now {
            return Err(ModelError::CatalogExpired);
        }
        for model in &catalog.models {
            if !model.source_url.starts_with("https://") {
                return Err(ModelError::InsecureSource);
            }
            if model.license.trim().is_empty() {
                return Err(ModelError::LicenseMissing);
            }
            if model.worker_abi != worker_abi {
                return Err(ModelError::AbiMismatch);
            }
            if !model
                .supported_targets
                .iter()
                .any(|candidate| candidate == target)
            {
                return Err(ModelError::UnsupportedTarget);
            }
            ModelArtifact {
                id: model.model_id.clone(),
                engine: "whisper.cpp".into(),
                license: model.license.clone(),
                size_bytes: model.size_bytes,
                sha256: model.sha256.clone(),
                runtime_abi: model.worker_abi.clone(),
            }
            .validate()?;
            if model.benchmark_result_digest.len() != 64
                || !model
                    .benchmark_result_digest
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(ModelError::Unpinned);
            }
        }
        Ok(catalog)
    }

    pub fn approved_model(&self, name: &str) -> Option<&CatalogModel> {
        self.models
            .iter()
            .find(|model| model.name == name && model.benchmark_approved)
    }
}

pub fn sha256_file(path: &Path) -> Result<String, io::Error> {
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
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn all_builtin_profiles_are_exactly_pinned_and_non_benchmarked() {
        let models = builtin_models();
        assert_eq!(
            models.map(|model| model.name),
            ["fast", "balanced", "accurate"]
        );
        for model in builtin_models() {
            assert_eq!(model.license, "MIT");
            assert_eq!(model.trust, "builtin_pinned");
            assert_eq!(model.benchmark_status, "not_run");
            assert_eq!(model.worker_abi, "openwhisper-worker-1");
            assert_eq!(model.pinned_revision.len(), 40);
            assert_eq!(model.sha256.len(), 64);
            assert!(model.source.contains(&model.pinned_revision));
            assert!(model.source.ends_with(&model.artifact_name));
            model
                .artifact()
                .validate_for_abi(BUILTIN_WORKER_ABI)
                .unwrap();
        }
    }

    #[derive(Clone)]
    struct TestResponse {
        status: u16,
        etag: &'static str,
        body: Vec<u8>,
        declared_length: Option<usize>,
        content_range: Option<String>,
    }

    async fn test_server(
        responder: impl Fn(usize, &str) -> TestResponse + Send + Sync + 'static,
    ) -> (String, Arc<AtomicUsize>, tokio::task::JoinHandle<()>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let count = Arc::new(AtomicUsize::new(0));
        let requests = count.clone();
        let responder = Arc::new(responder);
        let task = tokio::spawn(async move {
            loop {
                let Ok((mut stream, _)) = listener.accept().await else {
                    break;
                };
                let responder = responder.clone();
                let request_number = requests.fetch_add(1, Ordering::SeqCst);
                tokio::spawn(async move {
                    let mut request = vec![0_u8; 16 * 1024];
                    let read = stream.read(&mut request).await.unwrap_or(0);
                    let request = String::from_utf8_lossy(&request[..read]);
                    let response = responder(request_number, &request);
                    let reason = if response.status == 206 {
                        "Partial Content"
                    } else {
                        "OK"
                    };
                    let length = response.declared_length.unwrap_or(response.body.len());
                    let mut headers = format!(
                        "HTTP/1.1 {} {}\r\nContent-Length: {}\r\nETag: {}\r\nConnection: close\r\n",
                        response.status, reason, length, response.etag
                    );
                    if let Some(range) = response.content_range {
                        headers.push_str(&format!("Content-Range: {range}\r\n"));
                    }
                    headers.push_str("\r\n");
                    let _ = stream.write_all(headers.as_bytes()).await;
                    let _ = stream.write_all(&response.body).await;
                    let _ = stream.shutdown().await;
                });
            }
        });
        (format!("http://{address}/model.bin"), count, task)
    }

    fn fixture_artifact(bytes: &[u8]) -> ModelArtifact {
        let temp = tempfile::NamedTempFile::new().unwrap();
        fs::write(temp.path(), bytes).unwrap();
        ModelArtifact {
            id: "fixture".into(),
            engine: "whisper.cpp".into(),
            license: "MIT".into(),
            size_bytes: bytes.len() as u64,
            sha256: sha256_file(temp.path()).unwrap(),
            runtime_abi: "fixture-1".into(),
        }
    }

    fn test_download_options() -> DownloadOptions {
        DownloadOptions {
            allow_http: true,
            disk_margin_bytes: 0,
        }
    }

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
        assert_eq!(fs::read(&installed).unwrap(), b"verified model bytes");
        assert!(temp.path().join("models").read_dir().unwrap().all(|item| {
            !item
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with("partial")
        }));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(installed).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
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

    #[test]
    fn signed_catalog_rejects_tampering_expiry_and_rollback() {
        use ed25519_dalek::{Signer, SigningKey};
        let signing = SigningKey::from_bytes(&[7_u8; 32]);
        let bytes = serde_json::to_vec(&SignedCatalog {
            schema_version: 1,
            sequence: 4,
            expires_at: Utc::now() + chrono::Duration::days(1),
            models: vec![CatalogModel {
                name: "balanced".into(),
                model_id: "fixture".into(),
                source_url: "https://example.invalid/model".into(),
                license: "MIT".into(),
                size_bytes: 1,
                sha256: "00".repeat(32),
                worker_abi: "worker-1".into(),
                supported_targets: vec!["x86_64-unknown-linux-gnu".into()],
                benchmark_result_digest: "11".repeat(32),
                benchmark_approved: true,
            }],
        })
        .unwrap();
        let signature = signing.sign(&bytes).to_bytes();
        let public = signing.verifying_key().to_bytes();
        assert!(
            SignedCatalog::verify_exact_bytes(
                &bytes,
                &signature,
                &public,
                Utc::now(),
                4,
                "x86_64-unknown-linux-gnu",
                "worker-1"
            )
            .is_ok()
        );
        let mut tampered = bytes.clone();
        tampered.push(b' ');
        assert!(matches!(
            SignedCatalog::verify_exact_bytes(
                &tampered,
                &signature,
                &public,
                Utc::now(),
                4,
                "x86_64-unknown-linux-gnu",
                "worker-1"
            ),
            Err(ModelError::InvalidSignature)
        ));
        assert!(matches!(
            SignedCatalog::verify_exact_bytes(
                &bytes,
                &signature,
                &public,
                Utc::now(),
                5,
                "x86_64-unknown-linux-gnu",
                "worker-1"
            ),
            Err(ModelError::CatalogRollback)
        ));
        assert!(matches!(
            SignedCatalog::verify_exact_bytes(
                &bytes,
                &signature,
                &public,
                Utc::now() + chrono::Duration::days(2),
                4,
                "x86_64-unknown-linux-gnu",
                "worker-1"
            ),
            Err(ModelError::CatalogExpired)
        ));
        let wrong_public = SigningKey::from_bytes(&[8_u8; 32])
            .verifying_key()
            .to_bytes();
        assert!(matches!(
            SignedCatalog::verify_exact_bytes(
                &bytes,
                &signature,
                &wrong_public,
                Utc::now(),
                4,
                "x86_64-unknown-linux-gnu",
                "worker-1"
            ),
            Err(ModelError::InvalidSignature)
        ));
        assert!(matches!(
            SignedCatalog::verify_exact_bytes(
                &bytes,
                &signature,
                &public,
                Utc::now(),
                4,
                "x86_64-unknown-linux-gnu",
                "worker-2"
            ),
            Err(ModelError::AbiMismatch)
        ));
    }

    #[tokio::test]
    async fn downloader_handles_full_download_and_idempotent_reinstall() {
        let bytes = b"complete pinned model".to_vec();
        let expected = bytes.clone();
        let (url, requests, server) = test_server(move |_, _| TestResponse {
            status: 200,
            etag: "\"one\"",
            body: expected.clone(),
            declared_length: None,
            content_range: None,
        })
        .await;
        let temp = tempfile::tempdir().unwrap();
        let artifact = fixture_artifact(&bytes);
        let first = download_model(
            &artifact,
            &url,
            temp.path(),
            test_download_options(),
            |_, _| {},
        )
        .await
        .unwrap();
        assert!(!first.reused);
        let second = download_model(
            &artifact,
            &url,
            temp.path(),
            test_download_options(),
            |_, _| {},
        )
        .await
        .unwrap();
        assert!(second.reused);
        assert_eq!(requests.load(Ordering::SeqCst), 1);
        server.abort();
    }

    #[tokio::test]
    async fn downloader_resumes_and_restarts_when_range_is_ignored() {
        let bytes = b"abcdefghij".to_vec();
        let expected = bytes.clone();
        let (url, requests, server) = test_server(move |_, request| {
            if request.contains("range: bytes=3-") {
                TestResponse {
                    status: 200,
                    etag: "\"same\"",
                    body: expected.clone(),
                    declared_length: None,
                    content_range: None,
                }
            } else {
                panic!("expected a resume request: {request}")
            }
        })
        .await;
        let temp = tempfile::tempdir().unwrap();
        fs::write(temp.path().join(".fixture.partial"), b"abc").unwrap();
        fs::write(temp.path().join(".fixture.etag"), "\"same\"").unwrap();
        let artifact = fixture_artifact(&bytes);
        download_model(
            &artifact,
            &url,
            temp.path(),
            test_download_options(),
            |_, _| {},
        )
        .await
        .unwrap();
        assert_eq!(
            fs::read(artifact.canonical_path(temp.path())).unwrap(),
            bytes
        );
        assert_eq!(requests.load(Ordering::SeqCst), 1);
        server.abort();
    }

    #[tokio::test]
    async fn downloader_uses_valid_range_and_restarts_on_changed_etag() {
        let bytes = b"abcdefghij".to_vec();
        let valid_bytes = bytes.clone();
        let (valid_url, _, valid_server) = test_server(move |_, request| {
            assert!(request.contains("range: bytes=3-"));
            assert!(request.contains("if-range: \"same\""));
            TestResponse {
                status: 206,
                etag: "\"same\"",
                body: valid_bytes[3..].to_vec(),
                declared_length: None,
                content_range: Some("bytes 3-9/10".into()),
            }
        })
        .await;
        let valid_temp = tempfile::tempdir().unwrap();
        fs::write(valid_temp.path().join(".fixture.partial"), b"abc").unwrap();
        fs::write(valid_temp.path().join(".fixture.etag"), "\"same\"").unwrap();
        let artifact = fixture_artifact(&bytes);
        download_model(
            &artifact,
            &valid_url,
            valid_temp.path(),
            test_download_options(),
            |_, _| {},
        )
        .await
        .unwrap();
        valid_server.abort();

        let changed_bytes = bytes.clone();
        let (changed_url, count, changed_server) = test_server(move |number, request| {
            if number == 0 {
                assert!(request.contains("range: bytes=3-"));
                TestResponse {
                    status: 206,
                    etag: "\"changed\"",
                    body: changed_bytes[3..].to_vec(),
                    declared_length: None,
                    content_range: Some("bytes 3-9/10".into()),
                }
            } else {
                assert!(!request.contains("range:"));
                TestResponse {
                    status: 200,
                    etag: "\"changed\"",
                    body: changed_bytes.clone(),
                    declared_length: None,
                    content_range: None,
                }
            }
        })
        .await;
        let changed_temp = tempfile::tempdir().unwrap();
        fs::write(changed_temp.path().join(".fixture.partial"), b"abc").unwrap();
        fs::write(changed_temp.path().join(".fixture.etag"), "\"same\"").unwrap();
        download_model(
            &artifact,
            &changed_url,
            changed_temp.path(),
            test_download_options(),
            |_, _| {},
        )
        .await
        .unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
        changed_server.abort();
    }

    #[tokio::test]
    async fn downloader_preserves_interrupted_bytes_for_resume() {
        let bytes = b"abcdefghij".to_vec();
        let expected = bytes.clone();
        let (url, count, server) = test_server(move |number, request| {
            if number == 0 {
                TestResponse {
                    status: 200,
                    etag: "\"stable\"",
                    body: expected[..3].to_vec(),
                    declared_length: Some(expected.len()),
                    content_range: None,
                }
            } else {
                assert!(request.contains("range: bytes=3-"));
                TestResponse {
                    status: 206,
                    etag: "\"stable\"",
                    body: expected[3..].to_vec(),
                    declared_length: None,
                    content_range: Some("bytes 3-9/10".into()),
                }
            }
        })
        .await;
        let temp = tempfile::tempdir().unwrap();
        let artifact = fixture_artifact(&bytes);
        assert!(
            download_model(
                &artifact,
                &url,
                temp.path(),
                test_download_options(),
                |_, _| {}
            )
            .await
            .is_err()
        );
        assert_eq!(
            fs::read(temp.path().join(".fixture.partial")).unwrap(),
            b"abc"
        );
        download_model(
            &artifact,
            &url,
            temp.path(),
            test_download_options(),
            |_, _| {},
        )
        .await
        .unwrap();
        assert_eq!(count.load(Ordering::SeqCst), 2);
        server.abort();
    }

    #[tokio::test]
    async fn downloader_rejects_overflow_corruption_and_insufficient_disk() {
        let bytes = b"expected".to_vec();
        let too_large = b"expected-overflow".to_vec();
        let (overflow_url, _, overflow_server) = test_server(move |_, _| TestResponse {
            status: 200,
            etag: "\"overflow\"",
            body: too_large.clone(),
            declared_length: None,
            content_range: None,
        })
        .await;
        let artifact = fixture_artifact(&bytes);
        let overflow_temp = tempfile::tempdir().unwrap();
        assert!(matches!(
            download_model(
                &artifact,
                &overflow_url,
                overflow_temp.path(),
                test_download_options(),
                |_, _| {}
            )
            .await,
            Err(ModelError::DownloadOverflow)
        ));
        overflow_server.abort();

        let corrupt = b"corrupt!".to_vec();
        let (corrupt_url, _, corrupt_server) = test_server(move |_, _| TestResponse {
            status: 200,
            etag: "\"corrupt\"",
            body: corrupt.clone(),
            declared_length: None,
            content_range: None,
        })
        .await;
        let corrupt_temp = tempfile::tempdir().unwrap();
        let error = download_model(
            &artifact,
            &corrupt_url,
            corrupt_temp.path(),
            test_download_options(),
            |_, _| {},
        )
        .await
        .unwrap_err();
        assert!(matches!(error, ModelError::HashMismatch { .. }));
        assert!(corrupt_temp.path().read_dir().unwrap().any(|item| {
            item.unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".corrupt")
        }));
        corrupt_server.abort();

        let (disk_url, _, disk_server) =
            test_server(|_, _| panic!("disk preflight must happen before network access")).await;
        let disk_temp = tempfile::tempdir().unwrap();
        let options = DownloadOptions {
            allow_http: true,
            disk_margin_bytes: u64::MAX,
        };
        assert!(matches!(
            download_model(&artifact, &disk_url, disk_temp.path(), options, |_, _| {}).await,
            Err(ModelError::InsufficientDisk { .. })
        ));
        disk_server.abort();
    }
}
