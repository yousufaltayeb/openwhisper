pub mod audio;
pub mod capabilities;
pub mod clipboard;
pub mod config;
pub mod models;
pub mod paths;
pub mod processing;
pub mod providers;
pub mod state;
pub mod storage;

pub use capabilities::detect_capabilities;
pub use config::{
    AppConfig, AudioBackend, AudioConfig, DeliveryConfig, HistoryConfig, ModelConfig, OverlayMode,
    PrivacyConfig,
};
pub use paths::{AppPaths, LegacyDataReport};
pub use state::{CaptureCommand, CaptureCoordinator, CaptureState, DeliveryTarget, Mode};
pub use storage::{HistoryEntry, HistoryInput, InstalledModel, StateStore};
