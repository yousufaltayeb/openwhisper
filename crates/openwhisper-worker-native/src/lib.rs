use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const WORKER_ABI: &str = "openwhisper-worker-1";
pub const MAX_WORKER_MESSAGE_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct WorkerRequest {
    pub id: Uuid,
    pub generation: u64,
    #[serde(flatten)]
    pub command: WorkerCommand,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "command", rename_all = "snake_case")]
pub enum WorkerCommand {
    Probe,
    Transcribe {
        model_path: String,
        audio_path: String,
        language: String,
    },
    Cancel,
    Shutdown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WorkerResponse {
    Ready {
        abi: String,
    },
    Probe {
        id: Uuid,
        generation: u64,
        cpu: bool,
        backend: String,
    },
    Transcript {
        id: Uuid,
        generation: u64,
        text: String,
        language: String,
    },
    Cancelled {
        id: Uuid,
        generation: u64,
    },
    Error {
        id: Option<Uuid>,
        generation: u64,
        code: String,
        message: String,
    },
}

impl WorkerResponse {
    pub fn generation(&self) -> Option<u64> {
        match self {
            Self::Ready { .. } => None,
            Self::Probe { generation, .. }
            | Self::Transcript { generation, .. }
            | Self::Cancelled { generation, .. }
            | Self::Error { generation, .. } => Some(*generation),
        }
    }
}
