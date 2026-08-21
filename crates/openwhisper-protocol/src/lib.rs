//! Checked-in Rust representation of the canonical JSON protocol schema.
//! Regenerate both language bindings with `bun run protocol:generate`.

use std::io;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub const CURRENT_PROTOCOL_VERSION: u16 = 3;
pub const PREVIOUS_PROTOCOL_VERSION: u16 = 2;
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
pub const SCHEMA_SHA256: &str = "c29f39bc3760ad91c5c300c870828453a49357d70d54786e159cac2a2980b58f";

pub fn protocol_supported(version: u16) -> bool {
    matches!(
        version,
        CURRENT_PROTOCOL_VERSION | PREVIOUS_PROTOCOL_VERSION
    )
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMessage {
    Handshake {
        protocol_version: u16,
        client: String,
        client_version: String,
    },
    Request {
        id: String,
        method: String,
        #[serde(default)]
        params: Value,
    },
    Subscribe {
        #[serde(default)]
        after_sequence: u64,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMessage {
    HandshakeAck {
        protocol_version: u16,
        server_version: String,
        capabilities: Box<Capabilities>,
    },
    Response {
        id: String,
        result: Value,
    },
    Error {
        #[serde(skip_serializing_if = "Option::is_none")]
        id: Option<String>,
        error: RpcError,
    },
    Event {
        sequence: u64,
        event: String,
        data: Value,
    },
    Snapshot {
        sequence: u64,
        state: Value,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct RpcError {
    pub code: ErrorCode,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub action: Option<String>,
    pub retryable: bool,
}

impl RpcError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            detail: None,
            action: None,
            retryable: false,
        }
    }

    pub fn with_action(mut self, action: impl Into<String>) -> Self {
        self.action = Some(action.into());
        self
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    Usage,
    Configuration,
    DaemonUnavailable,
    UnsupportedCapability,
    PermissionDenied,
    ModelUnavailable,
    ProviderUnavailable,
    TranscriptionFailed,
    CleanupFailed,
    InsertionFailed,
    Io,
    Network,
    Cancelled,
    Conflict,
    Protocol,
    Internal,
}

impl ErrorCode {
    pub const fn exit_code(self) -> i32 {
        match self {
            Self::Usage | Self::Configuration | Self::Protocol | Self::Conflict => 2,
            Self::DaemonUnavailable => 3,
            Self::UnsupportedCapability | Self::PermissionDenied => 4,
            Self::ModelUnavailable | Self::ProviderUnavailable => 5,
            Self::TranscriptionFailed | Self::CleanupFailed | Self::Internal => 6,
            Self::InsertionFailed => 7,
            Self::Io | Self::Network => 8,
            Self::Cancelled => 130,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct Capabilities {
    pub audio: Capability,
    pub toggle_hotkey: Capability,
    pub push_to_talk: Capability,
    pub insertion: Capability,
    pub overlay: Capability,
    pub notifications: Capability,
    pub secrets: Capability,
    pub service_manager: Capability,
    pub accelerator: Capability,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct Capability {
    pub available: bool,
    pub backend: String,
    pub detail: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fallback: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TranscriptMode {
    Raw,
    Clean,
    Code,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Language {
    Auto,
    Ar,
    En,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TranscriptionSource {
    Microphone,
    File,
    Stdin,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum CaptureState {
    Idle,
    Capturing {
        session_id: uuid::Uuid,
        generation: u64,
        started_at: chrono::DateTime<chrono::Utc>,
        mode: TranscriptMode,
    },
    Transcribing {
        session_id: uuid::Uuid,
        generation: u64,
        mode: TranscriptMode,
    },
    Processing {
        session_id: uuid::Uuid,
        generation: u64,
        mode: TranscriptMode,
    },
    Delivering {
        session_id: uuid::Uuid,
        generation: u64,
    },
    Failed {
        session_id: Option<uuid::Uuid>,
        generation: u64,
        message: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct ReadinessBlocker {
    pub capability: String,
    pub code: String,
    pub detail: String,
    pub action: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
pub struct SystemStatus {
    pub daemon: String,
    pub version: String,
    pub protocol: u16,
    pub capture: CaptureState,
    pub capture_available: bool,
    pub blockers: Vec<ReadinessBlocker>,
    pub mode: TranscriptMode,
    pub language: Language,
    pub local_only: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
pub struct DoctorResult {
    pub capabilities: Capabilities,
    pub blockers: Vec<ReadinessBlocker>,
    pub legacy: Value,
    pub data: Value,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ModelTrust {
    BuiltinPinned,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BenchmarkStatus {
    NotRun,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct ModelInfo {
    pub name: String,
    pub model_id: String,
    pub installed: bool,
    pub selected: bool,
    pub installing: bool,
    pub trust: ModelTrust,
    pub benchmark_status: BenchmarkStatus,
    pub source: String,
    pub license: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub worker_abi: String,
    pub artifact_name: String,
    pub pinned_revision: String,
    pub verification_state: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct ModelDownloadProgress {
    pub name: String,
    pub downloaded_bytes: u64,
    pub total_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct TranscriptionResult {
    pub session_id: uuid::Uuid,
    pub generation: u64,
    pub raw_text: String,
    pub final_text: String,
    pub language: Language,
    pub mode: TranscriptMode,
    pub duration_ms: u64,
    pub source: TranscriptionSource,
    pub history_id: Option<uuid::Uuid>,
    pub inserted: bool,
    pub inserted_bytes: u64,
    pub insertion_status: InsertionStatus,
    pub copied: bool,
    pub insertion_method: String,
    pub requested_backend: String,
    pub actual_backend: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gpu_device: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backend_fallback_reason: Option<String>,
    pub streaming_latency_ms: u64,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum InsertionStatus {
    NotRequested,
    Active,
    Complete,
    Suspended,
    Partial,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
pub struct TranscriptionPreviewEvent {
    pub generation: u64,
    pub text: String,
    pub language: Language,
    pub latency_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct TranscriptionCommitEvent {
    pub generation: u64,
    pub delta: String,
    pub committed: String,
    pub final_commit: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
pub struct RecordingLevelEvent {
    pub generation: u64,
    pub dbfs: f32,
    pub peak_dbfs: f32,
    pub signal: bool,
    pub clipping: bool,
    pub bytes_captured: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
pub struct InsertionStateEvent {
    pub generation: u64,
    pub status: InsertionStatus,
    pub inserted_bytes: u64,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq)]
#[serde(tag = "event", content = "data", rename_all = "snake_case")]
pub enum TypedEvent {
    CaptureChanged(Value),
    ConfigChanged(Value),
    ModelProgress(ModelDownloadProgress),
    TranscriptionPreview(TranscriptionPreviewEvent),
    TranscriptionCommit(TranscriptionCommitEvent),
    RecordingLevel(RecordingLevelEvent),
    InsertionState(InsertionStateEvent),
    ResultAvailable {
        session_id: uuid::Uuid,
        generation: u64,
    },
}

#[derive(Debug, Error)]
pub enum FrameError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("frame size {actual} exceeds the {maximum} byte limit")]
    Oversized { actual: usize, maximum: usize },
    #[error("frame is not valid UTF-8 JSON: {0}")]
    Json(#[from] serde_json::Error),
}

pub async fn read_frame<R, T>(reader: &mut R) -> Result<Option<T>, FrameError>
where
    R: AsyncRead + Unpin,
    T: for<'de> Deserialize<'de>,
{
    let length = match reader.read_u32().await {
        Ok(length) => length as usize,
        Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if length > MAX_FRAME_BYTES {
        return Err(FrameError::Oversized {
            actual: length,
            maximum: MAX_FRAME_BYTES,
        });
    }
    let mut payload = vec![0; length];
    reader.read_exact(&mut payload).await?;
    Ok(Some(serde_json::from_slice(&payload)?))
}

pub async fn write_frame<W, T>(writer: &mut W, message: &T) -> Result<(), FrameError>
where
    W: AsyncWrite + Unpin,
    T: Serialize,
{
    let payload = serde_json::to_vec(message)?;
    if payload.len() > MAX_FRAME_BYTES {
        return Err(FrameError::Oversized {
            actual: payload.len(),
            maximum: MAX_FRAME_BYTES,
        });
    }
    writer.write_u32(payload.len() as u32).await?;
    writer.write_all(&payload).await?;
    writer.flush().await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    #[tokio::test]
    async fn frames_round_trip() {
        let (mut client, mut server) = duplex(4096);
        let message = ClientMessage::Request {
            id: "r1".into(),
            method: "system.status".into(),
            params: Value::Null,
        };
        let expected = message.clone();
        let writer = tokio::spawn(async move { write_frame(&mut client, &message).await });
        let actual: ClientMessage = read_frame(&mut server).await.unwrap().unwrap();
        writer.await.unwrap().unwrap();
        assert_eq!(actual, expected);
    }

    #[tokio::test]
    async fn oversized_frame_is_rejected_before_allocation() {
        let (mut client, mut server) = duplex(16);
        tokio::spawn(async move {
            client
                .write_u32((MAX_FRAME_BYTES + 1) as u32)
                .await
                .unwrap();
        });
        assert!(matches!(
            read_frame::<_, ClientMessage>(&mut server).await,
            Err(FrameError::Oversized { .. })
        ));
    }

    #[test]
    fn accepts_current_and_previous_protocol_only() {
        assert!(protocol_supported(CURRENT_PROTOCOL_VERSION));
        assert!(protocol_supported(PREVIOUS_PROTOCOL_VERSION));
        assert!(!protocol_supported(CURRENT_PROTOCOL_VERSION + 1));
    }

    #[test]
    fn every_error_has_a_stable_exit_code() {
        assert_eq!(ErrorCode::DaemonUnavailable.exit_code(), 3);
        assert_eq!(ErrorCode::Cancelled.exit_code(), 130);
        assert_eq!(ErrorCode::InsertionFailed.exit_code(), 7);
    }
}
