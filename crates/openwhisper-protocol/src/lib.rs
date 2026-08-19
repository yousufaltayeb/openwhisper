//! Checked-in Rust representation of the canonical JSON protocol schema.
//! Regenerate both language bindings with `bun run protocol:generate`.

use std::io;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub const CURRENT_PROTOCOL_VERSION: u16 = 2;
pub const PREVIOUS_PROTOCOL_VERSION: u16 = 1;
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
pub const SCHEMA_SHA256: &str = "bd57bc6d79cc96ca0ece63b31701477d8653c2706dc7a44319e954353f0841a7";

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
