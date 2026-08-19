use std::fs::File;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};

use openwhisper_core::{
    AppConfig, AppPaths, CaptureCoordinator, HistoryInput, Mode, StateStore, detect_capabilities,
};
use openwhisper_protocol::{ErrorCode, RpcError, ServerMessage};
use serde_json::{Value, json};
use tokio::sync::{Notify, broadcast};
use uuid::Uuid;

pub mod worker;

#[derive(Clone)]
pub struct DaemonState {
    pub paths: AppPaths,
    pub config: Arc<RwLock<AppConfig>>,
    pub store: Arc<StateStore>,
    pub capture: Arc<Mutex<CaptureCoordinator>>,
    capture_simulation: bool,
    sequence: Arc<AtomicU64>,
    events: broadcast::Sender<ServerMessage>,
    pub shutdown: Arc<Notify>,
}

impl DaemonState {
    pub fn initialize(paths: AppPaths) -> anyhow::Result<Self> {
        paths.ensure()?;
        let config = AppConfig::load_or_create(&paths)?;
        let store = StateStore::open(&paths.state_file())?;
        store.prune_history(config.history.retention_days)?;
        let (events, _) = broadcast::channel(128);
        Ok(Self {
            paths,
            config: Arc::new(RwLock::new(config)),
            store: Arc::new(store),
            capture: Arc::new(Mutex::new(CaptureCoordinator::default())),
            capture_simulation: std::env::var("OPENWHISPER_SIMULATE_CAPTURE").as_deref() == Ok("1"),
            sequence: Arc::new(AtomicU64::new(0)),
            events,
            shutdown: Arc::new(Notify::new()),
        })
    }

    pub fn subscribe(&self) -> broadcast::Receiver<ServerMessage> {
        self.events.subscribe()
    }

    pub fn snapshot(&self) -> ServerMessage {
        ServerMessage::Snapshot {
            sequence: self.sequence.load(Ordering::SeqCst),
            state: self.status(),
        }
    }

    pub fn status(&self) -> Value {
        let capture = self.capture.lock().expect("capture state poisoned");
        let config = self.config.read().expect("config state poisoned");
        json!({
            "daemon": "running",
            "version": env!("CARGO_PKG_VERSION"),
            "protocol": openwhisper_protocol::CURRENT_PROTOCOL_VERSION,
            "capture": capture.state(),
            "capture_available": self.capture_simulation,
            "mode": config.mode,
            "language": config.language,
            "local_only": config.privacy.local_only,
        })
    }

    pub fn dispatch(&self, method: &str, params: Value) -> Result<Value, RpcError> {
        match method {
            "system.status" | "record.status" => Ok(self.status()),
            "system.doctor" => Ok(json!({
                "capabilities": detect_capabilities(),
                "legacy": self.paths.detect_legacy(),
                "data": {
                    "config": self.paths.config_file(),
                    "state": self.paths.state_file(),
                    "versioned": true
                }
            })),
            "system.shutdown" => {
                self.shutdown.notify_waiters();
                Ok(json!({"stopping": true}))
            }
            "record.start" => self.record_start(params),
            "record.stop" => self.record_stop(),
            "record.toggle" => self.record_toggle(params),
            "record.cancel" => self.record_cancel(),
            "history.list" => {
                let limit = usize_param(&params, "limit", 50).min(1000);
                Ok(json!(self.store.list_history(limit).map_err(internal)?))
            }
            "history.search" => {
                let query = string_param(&params, "query")?;
                let limit = usize_param(&params, "limit", 50).min(1000);
                Ok(json!(self.store.search_history(query, limit).map_err(internal)?))
            }
            "history.show" => {
                let id = uuid_param(&params, "id")?;
                Ok(json!(self.store.show_history(id).map_err(not_found)?))
            }
            "history.delete" => {
                let id = uuid_param(&params, "id")?;
                Ok(json!({"deleted": self.store.delete_history(id).map_err(internal)?}))
            }
            "history.clear" => {
                if params.get("confirmed").and_then(Value::as_bool) != Some(true) {
                    return Err(RpcError::new(ErrorCode::Usage, "history clear requires --yes")
                        .with_action("Run `openwhisper history clear --yes` to delete 1.0 transcript history. Legacy data is never touched."));
                }
                Ok(json!({"deleted": self.store.clear_history().map_err(internal)?}))
            }
            "history.copy" => Err(unsupported("clipboard copy is not linked in this alpha build", "Use `openwhisper history show <id> --plain` and copy the result.")),
            "history.export" => Err(unsupported("history export is not linked in this alpha build", "Use `openwhisper history list --jsonl` and redirect stdout.")),
            "history.add_fixture" => self.add_history_fixture(params),
            "modes.list" => Ok(json!([
                {"name": "raw", "description": "Trim edges only; preserve the transcript."},
                {"name": "clean", "description": "Deterministic whitespace and replacement cleanup."},
                {"name": "code", "description": "Preserve lines and code-sensitive whitespace."}
            ])),
            "modes.show" => {
                let mode = parse_mode(string_param(&params, "name")?)?;
                Ok(json!({"name": mode, "selected": self.config.read().expect("config poisoned").mode == mode}))
            }
            "modes.select" => {
                let mode = parse_mode(string_param(&params, "name")?)?;
                self.update_config(|config| config.mode = mode)?;
                self.emit("config.changed", json!({"mode": mode}));
                Ok(json!({"selected": mode}))
            }
            "vocab.list" => Ok(json!(self.store.list_strings("vocabulary").map_err(internal)?)),
            "vocab.add" => {
                let term = string_param(&params, "term")?;
                self.store.put_string("vocabulary", term, None).map_err(internal)?;
                Ok(json!({"added": term}))
            }
            "vocab.remove" => {
                let term = string_param(&params, "term")?;
                Ok(json!({"removed": self.store.remove_string("vocabulary", term).map_err(internal)?}))
            }
            "vocab.import" | "vocab.export" => Err(unsupported("vocabulary file import/export is not linked in this alpha build", "Use vocab list/add/remove commands.")),
            "snippets.list" => Ok(json!(self.store.list_strings("snippets").map_err(internal)?)),
            "snippets.add" => {
                let name = string_param(&params, "name")?;
                let body = string_param(&params, "body")?;
                self.store.put_string("snippets", name, Some(body)).map_err(internal)?;
                Ok(json!({"added": name}))
            }
            "snippets.remove" => {
                let name = string_param(&params, "name")?;
                Ok(json!({"removed": self.store.remove_string("snippets", name).map_err(internal)?}))
            }
            "snippets.run" => {
                let name = string_param(&params, "name")?;
                let item = self.store.list_strings("snippets").map_err(internal)?
                    .into_iter().find(|(candidate, _)| candidate == name)
                    .ok_or_else(|| RpcError::new(ErrorCode::Configuration, "snippet was not found"))?;
                Ok(json!({"name": item.0, "text": item.1.unwrap_or_default(), "inserted": false}))
            }
            "snippets.import" | "snippets.export" => Err(unsupported("snippet file import/export is not linked in this alpha build", "Use snippets list/add/remove commands.")),
            "config.list" => Ok(serde_json::to_value(self.config.read().expect("config poisoned").clone()).map_err(internal)?),
            "config.get" => self.config_get(params),
            "config.set" => self.config_set(params),
            "models.list" => Ok(model_catalog()),
            "models.install" | "models.remove" | "models.verify" | "models.select" | "models.import" => Err(RpcError::new(
                ErrorCode::ModelUnavailable,
                "the signed model catalog is not approved for this alpha build",
            ).with_action("Review docs/rewrite/RELEASE_STATUS.md; no model download will start.")),
            "providers.list" => Ok(provider_catalog()),
            "providers.configure" | "providers.test" | "providers.unset" => Err(RpcError::new(
                ErrorCode::ProviderUnavailable,
                "cloud provider credentials are disabled until an approved secure-store adapter is available",
            ).with_action("Use local-only mode; no credential was read or changed.")),
            "system.setup" | "service.install" | "service.restart" | "service.uninstall" => Err(unsupported(
                "the signed per-user service installer is not linked in this alpha build",
                "Use foreground development mode or a native package after its platform gate passes.",
            )),
            "system.logs" => Ok(json!({"available": false, "transcript_bearing": false, "message": "No persistent service log is configured in this alpha build."})),
            "system.update" => Ok(json!({"automatic": false, "provenance": "source_checkout", "message": "OpenWhisper never checks for updates in the background. Update this checkout with your source-control workflow."})),
            "transcribe.file" => Err(RpcError::new(
                ErrorCode::ModelUnavailable,
                "no verified transcription model is installed",
            ).with_action("Run `openwhisper models install balanced` after reviewing the model license and disk requirements.")),
            _ => Err(RpcError::new(ErrorCode::Usage, format!("unknown method: {method}"))),
        }
    }

    fn record_start(&self, params: Value) -> Result<Value, RpcError> {
        if !self.capture_simulation {
            return Err(unsupported(
                "native microphone capture is not linked in this alpha build",
                "Use doctor to inspect the platform gate. No microphone was opened.",
            ));
        }
        let mode = params
            .get("mode")
            .and_then(Value::as_str)
            .map(parse_mode)
            .transpose()?
            .unwrap_or_else(|| self.config.read().expect("config poisoned").mode);
        let mut capture = self
            .capture
            .lock()
            .map_err(|_| internal("capture state is poisoned"))?;
        let id = capture.start(mode, None).map_err(conflict)?;
        let state = serde_json::to_value(capture.state()).map_err(internal)?;
        drop(capture);
        self.emit("recording.changed", state);
        Ok(json!({"session_id": id, "state": "capturing"}))
    }

    fn record_stop(&self) -> Result<Value, RpcError> {
        let mut capture = self
            .capture
            .lock()
            .map_err(|_| internal("capture state is poisoned"))?;
        let generation = capture.stop().map_err(conflict)?;
        let state = serde_json::to_value(capture.state()).map_err(internal)?;
        drop(capture);
        self.emit("recording.changed", state);
        Ok(json!({"generation": generation, "state": "transcribing"}))
    }

    fn record_toggle(&self, params: Value) -> Result<Value, RpcError> {
        if !self.capture_simulation
            && matches!(
                self.capture
                    .lock()
                    .map_err(|_| internal("capture state is poisoned"))?
                    .state(),
                openwhisper_core::CaptureState::Idle
                    | openwhisper_core::CaptureState::Failed { .. }
            )
        {
            return Err(unsupported(
                "native microphone capture is not linked in this alpha build",
                "Use doctor to inspect the platform gate. No microphone was opened.",
            ));
        }
        let mode = params
            .get("mode")
            .and_then(Value::as_str)
            .map(parse_mode)
            .transpose()?
            .unwrap_or_else(|| self.config.read().expect("config poisoned").mode);
        let mut capture = self
            .capture
            .lock()
            .map_err(|_| internal("capture state is poisoned"))?;
        let id = capture.toggle(mode, None).map_err(conflict)?;
        let state = serde_json::to_value(capture.state()).map_err(internal)?;
        drop(capture);
        self.emit("recording.changed", state.clone());
        Ok(json!({"session_id": id, "capture": state}))
    }

    fn record_cancel(&self) -> Result<Value, RpcError> {
        let mut capture = self
            .capture
            .lock()
            .map_err(|_| internal("capture state is poisoned"))?;
        capture.cancel().map_err(conflict)?;
        drop(capture);
        self.emit("recording.changed", json!({"phase": "idle"}));
        Ok(json!({"cancelled": true}))
    }

    fn add_history_fixture(&self, params: Value) -> Result<Value, RpcError> {
        if std::env::var_os("OPENWHISPER_TESTING").is_none() {
            return Err(RpcError::new(
                ErrorCode::UnsupportedCapability,
                "test fixture method is disabled",
            ));
        }
        let text = string_param(&params, "text")?.to_owned();
        Ok(json!(
            self.store
                .add_history(HistoryInput {
                    raw_text: text.clone(),
                    final_text: text,
                    mode: Mode::Raw,
                    language: "auto".into(),
                    duration_ms: 0,
                    inserted: false,
                    source: "fixture".into(),
                })
                .map_err(internal)?
        ))
    }

    fn config_get(&self, params: Value) -> Result<Value, RpcError> {
        let key = string_param(&params, "key")?;
        let config = serde_json::to_value(self.config.read().expect("config poisoned").clone())
            .map_err(internal)?;
        dotted_get(&config, key).cloned().ok_or_else(|| {
            RpcError::new(
                ErrorCode::Configuration,
                format!("unknown config key: {key}"),
            )
        })
    }

    fn config_set(&self, params: Value) -> Result<Value, RpcError> {
        let key = string_param(&params, "key")?;
        let value = params
            .get("value")
            .cloned()
            .ok_or_else(|| RpcError::new(ErrorCode::Usage, "missing parameter: value"))?;
        match key {
            "mode" => {
                let mode = value
                    .as_str()
                    .ok_or_else(|| RpcError::new(ErrorCode::Configuration, "mode must be a string"))
                    .and_then(parse_mode)?;
                self.update_config(|config| config.mode = mode)?;
            }
            "language" => {
                let language = value
                    .as_str()
                    .ok_or_else(|| {
                        RpcError::new(ErrorCode::Configuration, "language must be a string")
                    })?
                    .to_owned();
                self.update_config(|config| config.language = language)?;
            }
            "history.retention_days" => {
                let days = value
                    .as_u64()
                    .and_then(|v| u16::try_from(v).ok())
                    .ok_or_else(|| {
                        RpcError::new(
                            ErrorCode::Configuration,
                            "retention_days must be an unsigned 16-bit integer",
                        )
                    })?;
                self.update_config(|config| config.history.retention_days = days)?;
                self.store.prune_history(days).map_err(internal)?;
            }
            "history.enabled" => {
                let enabled = value.as_bool().ok_or_else(|| {
                    RpcError::new(
                        ErrorCode::Configuration,
                        "history.enabled must be a boolean",
                    )
                })?;
                self.update_config(|config| config.history.enabled = enabled)?;
            }
            "privacy.local_only" => {
                let local = value.as_bool().ok_or_else(|| {
                    RpcError::new(
                        ErrorCode::Configuration,
                        "privacy.local_only must be a boolean",
                    )
                })?;
                self.update_config(|config| config.privacy.local_only = local)?;
            }
            _ => {
                return Err(RpcError::new(
                    ErrorCode::Configuration,
                    format!("config key is not writable: {key}"),
                ));
            }
        }
        self.emit("config.changed", json!({"key": key, "value": value}));
        Ok(json!({"key": key, "value": value}))
    }

    fn update_config(&self, update: impl FnOnce(&mut AppConfig)) -> Result<(), RpcError> {
        let mut config = self
            .config
            .write()
            .map_err(|_| internal("config state is poisoned"))?;
        update(&mut config);
        config.save(&self.paths.config_file()).map_err(internal)
    }

    fn emit(&self, event: &str, data: Value) {
        let sequence = self.sequence.fetch_add(1, Ordering::SeqCst) + 1;
        let _ = self.events.send(ServerMessage::Event {
            sequence,
            event: event.into(),
            data,
        });
    }
}

pub struct InstanceGuard {
    #[allow(dead_code)]
    file: File,
    pub path: PathBuf,
}

impl InstanceGuard {
    pub fn acquire(paths: &AppPaths) -> anyhow::Result<Self> {
        use fs2::FileExt;
        use std::fs::OpenOptions;
        let path = paths.lock_file();
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&path)?;
        file.try_lock_exclusive()
            .map_err(|_| anyhow::anyhow!("openwhisperd is already running for this user"))?;
        Ok(Self { file, path })
    }
}

fn string_param<'a>(params: &'a Value, name: &str) -> Result<&'a str, RpcError> {
    params.get(name).and_then(Value::as_str).ok_or_else(|| {
        RpcError::new(
            ErrorCode::Usage,
            format!("missing string parameter: {name}"),
        )
    })
}

fn usize_param(params: &Value, name: &str, default: usize) -> usize {
    params
        .get(name)
        .and_then(Value::as_u64)
        .and_then(|v| usize::try_from(v).ok())
        .unwrap_or(default)
}

fn uuid_param(params: &Value, name: &str) -> Result<Uuid, RpcError> {
    Uuid::parse_str(string_param(params, name)?)
        .map_err(|_| RpcError::new(ErrorCode::Usage, format!("{name} must be a UUID")))
}

fn parse_mode(value: &str) -> Result<Mode, RpcError> {
    match value {
        "raw" => Ok(Mode::Raw),
        "clean" => Ok(Mode::Clean),
        "code" => Ok(Mode::Code),
        _ => Err(RpcError::new(
            ErrorCode::Configuration,
            "mode must be raw, clean, or code",
        )),
    }
}

fn dotted_get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    key.split('.')
        .try_fold(value, |current, part| current.get(part))
}

fn internal(error: impl std::fmt::Display) -> RpcError {
    let mut rpc = RpcError::new(
        ErrorCode::Internal,
        "the daemon could not complete the request",
    );
    rpc.detail = Some(error.to_string());
    rpc
}

fn conflict(error: impl std::fmt::Display) -> RpcError {
    RpcError::new(ErrorCode::Conflict, error.to_string())
}
fn not_found(error: impl std::fmt::Display) -> RpcError {
    RpcError::new(ErrorCode::Configuration, error.to_string())
}

fn unsupported(message: &str, action: &str) -> RpcError {
    RpcError::new(ErrorCode::UnsupportedCapability, message).with_action(action)
}

fn model_catalog() -> Value {
    json!([{
        "id": "balanced", "engine": "whisper.cpp", "model": "large-v3-turbo-q5",
        "channel": "release_candidate", "installed": false, "bundled": false,
        "license": "See the signed model catalog before installation", "size_bytes": null,
        "sha256": null, "stable_blocked": true,
        "reason": "Release hash, Arabic benchmark, and latency gates are not yet approved"
    }])
}

fn provider_catalog() -> Value {
    json!([
        {"id": "local", "kind": "local", "enabled": false, "network": false, "reason": "no verified model installed"},
        {"id": "cohere", "kind": "byok", "enabled": false, "network": true},
        {"id": "openai", "kind": "byok", "enabled": false, "network": true},
        {"id": "groq", "kind": "byok", "enabled": false, "network": true},
        {"id": "deepgram", "kind": "byok", "enabled": false, "network": true}
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    use openwhisper_core::CaptureState;

    fn daemon() -> (tempfile::TempDir, DaemonState) {
        let temp = tempfile::tempdir().unwrap();
        let mut state = DaemonState::initialize(AppPaths::under(temp.path())).unwrap();
        state.capture_simulation = true;
        (temp, state)
    }

    #[test]
    fn dispatches_capture_and_config_contracts() {
        let (_temp, daemon) = daemon();
        daemon
            .dispatch("record.start", json!({"mode": "clean"}))
            .unwrap();
        assert!(matches!(
            *daemon.capture.lock().unwrap().state(),
            CaptureState::Capturing {
                mode: Mode::Clean,
                ..
            }
        ));
        assert!(daemon.dispatch("record.start", json!({})).is_err());
        daemon.dispatch("record.cancel", json!({})).unwrap();
        daemon
            .dispatch(
                "config.set",
                json!({"key": "history.retention_days", "value": 0}),
            )
            .unwrap();
        assert_eq!(
            daemon
                .dispatch("config.get", json!({"key": "history.retention_days"}))
                .unwrap(),
            json!(0)
        );
    }

    #[test]
    fn transcript_fixture_is_disabled_in_production() {
        let (_temp, daemon) = daemon();
        let error = daemon
            .dispatch("history.add_fixture", json!({"text": "secret"}))
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::UnsupportedCapability);
    }

    #[test]
    fn unknown_methods_are_structured_usage_errors() {
        let (_temp, daemon) = daemon();
        let error = daemon.dispatch("meetings.start", Value::Null).unwrap_err();
        assert_eq!(error.code.exit_code(), 2);
    }

    #[test]
    fn history_clear_requires_explicit_confirmation() {
        let (_temp, daemon) = daemon();
        let error = daemon.dispatch("history.clear", json!({})).unwrap_err();
        assert_eq!(error.code, ErrorCode::Usage);
        assert_eq!(
            daemon
                .dispatch("history.clear", json!({"confirmed": true}))
                .unwrap(),
            json!({"deleted": 0})
        );
    }

    #[test]
    fn documented_but_gated_commands_return_capability_errors() {
        let (_temp, daemon) = daemon();
        for method in ["service.install", "vocab.import", "snippets.export"] {
            assert_eq!(
                daemon.dispatch(method, json!({})).unwrap_err().code,
                ErrorCode::UnsupportedCapability
            );
        }
        assert_eq!(
            daemon
                .dispatch("models.install", json!({}))
                .unwrap_err()
                .code,
            ErrorCode::ModelUnavailable
        );
        assert_eq!(
            daemon
                .dispatch("providers.configure", json!({}))
                .unwrap_err()
                .code,
            ErrorCode::ProviderUnavailable
        );
    }
}
