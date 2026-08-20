use std::collections::HashMap;
use std::fs::File;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant, SystemTime};

use openwhisper_core::audio::{ActiveCapture, cleanup_stale_sessions};
use openwhisper_core::models::{
    BuiltinModelManifest, DownloadOptions, ModelError, builtin_balanced_model, download_model,
    quarantine_file, verify_file,
};
use openwhisper_core::processing::TextProcessor;
use openwhisper_core::{
    AppConfig, AppPaths, CaptureCoordinator, HistoryInput, InstalledModel, Mode, StateStore,
    detect_capabilities,
};
use openwhisper_protocol::{
    BenchmarkStatus, ErrorCode, Language, ModelDownloadProgress, ModelInfo, ModelTrust, RpcError,
    ServerMessage, TranscriptMode, TranscriptionResult, TranscriptionSource,
};
use openwhisper_worker_native::{WORKER_ABI, WorkerCommand, WorkerRequest, WorkerResponse};
use serde_json::{Value, json};
use tokio::sync::{Mutex as AsyncMutex, Notify, broadcast};
use tokio::task::JoinHandle;
use uuid::Uuid;
use worker::{SupervisorError, WorkerSupervisor};

pub mod worker;

#[derive(Clone)]
pub struct DaemonState {
    pub paths: AppPaths,
    pub config: Arc<RwLock<AppConfig>>,
    pub store: Arc<StateStore>,
    pub capture: Arc<Mutex<CaptureCoordinator>>,
    runtime: Arc<AsyncMutex<RuntimeOwner>>,
    processing: Arc<Mutex<Option<JoinHandle<()>>>>,
    results: Arc<Mutex<HashMap<Uuid, CachedResult>>>,
    result_errors: Arc<Mutex<HashMap<Uuid, (Instant, RpcError)>>>,
    result_ready: Arc<Mutex<HashMap<Uuid, Arc<Notify>>>>,
    model_install_lock: Arc<AsyncMutex<()>>,
    model_progress: Arc<Mutex<Option<ModelDownloadProgress>>>,
    verified_model_fingerprints: Arc<Mutex<HashMap<String, (u64, Option<SystemTime>)>>>,
    #[cfg(any(test, feature = "test-capture"))]
    test_capture: bool,
    sequence: Arc<AtomicU64>,
    events: broadcast::Sender<ServerMessage>,
    pub shutdown: Arc<Notify>,
}

struct RuntimeOwner {
    recorder: Option<ActiveCapture>,
    worker: Option<WorkerSupervisor>,
}

#[derive(Clone)]
struct CachedResult {
    completed_at: Instant,
    result: TranscriptionResult,
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
            runtime: Arc::new(AsyncMutex::new(RuntimeOwner {
                recorder: None,
                worker: None,
            })),
            processing: Arc::new(Mutex::new(None)),
            results: Arc::new(Mutex::new(HashMap::new())),
            result_errors: Arc::new(Mutex::new(HashMap::new())),
            result_ready: Arc::new(Mutex::new(HashMap::new())),
            model_install_lock: Arc::new(AsyncMutex::new(())),
            model_progress: Arc::new(Mutex::new(None)),
            verified_model_fingerprints: Arc::new(Mutex::new(HashMap::new())),
            #[cfg(any(test, feature = "test-capture"))]
            test_capture: cfg!(test),
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
        let config = self.config.read().expect("config state poisoned").clone();
        let blockers = self.readiness_blockers();
        let capabilities = detect_capabilities();
        json!({
            "daemon": "running",
            "version": env!("CARGO_PKG_VERSION"),
            "protocol": openwhisper_protocol::CURRENT_PROTOCOL_VERSION,
            "capture": capture.state(),
            "capture_available": blockers.is_empty(),
            "blockers": blockers,
            "audio_backend": capabilities.audio.backend,
            "model": config.model.selected,
            "mode": config.mode,
            "language": config.language,
            "local_only": config.privacy.local_only,
            "model_installing": self.model_progress.lock().ok().is_some_and(|progress| progress.is_some()),
        })
    }

    pub async fn dispatch(&self, method: &str, params: Value) -> Result<Value, RpcError> {
        match method {
            "system.status" | "record.status" => Ok(self.status()),
            "system.doctor" => Ok(json!({
                "capabilities": detect_capabilities(),
                "blockers": self.readiness_blockers(),
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
            "record.start" => self.record_start(params).await,
            "record.stop" => self.record_stop(params).await,
            "record.result" => self.record_result(params),
            "record.toggle" => self.record_toggle(params).await,
            "record.cancel" => self.record_cancel().await,
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
            "models.list" => Ok(json!(self.model_list()?)),
            "models.import" => self.model_import(params).await,
            "models.install" => self.model_install(params).await,
            "models.remove" => self.model_remove(params).await,
            "models.verify" => self.model_verify(params),
            "models.select" => self.model_select(params),
            "providers.list" => Ok(provider_catalog(
                self.model_list()?
                    .first()
                    .is_some_and(|model| model.installed && model.selected),
            )),
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
            "transcribe.file" => self.transcribe_file(params).await,
            _ => Err(RpcError::new(ErrorCode::Usage, format!("unknown method: {method}"))),
        }
    }

    async fn record_start(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["mode"])?;
        let mode = params
            .get("mode")
            .and_then(Value::as_str)
            .map(parse_mode)
            .transpose()?
            .unwrap_or_else(|| self.config.read().expect("config poisoned").mode);
        {
            let capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            if !matches!(
                capture.state(),
                openwhisper_core::CaptureState::Idle
                    | openwhisper_core::CaptureState::Failed { .. }
            ) {
                return Err(conflict("a capture is already active"));
            }
        }

        #[cfg(any(test, feature = "test-capture"))]
        let recorder = if self.test_capture {
            None
        } else {
            Some(self.start_recorder().await?)
        };
        #[cfg(not(any(test, feature = "test-capture")))]
        let recorder = Some(self.start_recorder().await?);
        let session_id = recorder
            .as_ref()
            .map(|active| active.session_id)
            .unwrap_or_else(Uuid::new_v4);
        let transition = (|| {
            let mut capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            let id = capture
                .start_session(session_id, mode, None)
                .map_err(conflict)?;
            let state = serde_json::to_value(capture.state()).map_err(internal)?;
            let generation = capture.sequence();
            Ok::<_, RpcError>((id, state, generation))
        })();
        let (id, state, generation) = match transition {
            Ok(transition) => transition,
            Err(error) => {
                if let Some(recorder) = recorder {
                    recorder.cancel().await;
                }
                return Err(error);
            }
        };
        if let Some(recorder) = recorder {
            self.runtime.lock().await.recorder = Some(recorder);
        }
        self.emit("recording.changed", state);
        let maximum = self
            .config
            .read()
            .map_err(|_| internal("config state is poisoned"))?
            .audio
            .max_recording_seconds;
        let timeout_state = self.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_secs(u64::from(maximum))).await;
            let still_active = timeout_state.capture.lock().ok().is_some_and(|capture| {
                matches!(capture.state(), openwhisper_core::CaptureState::Capturing { generation: active, .. } if *active == generation)
            });
            if still_active {
                let _ = timeout_state.record_cancel().await;
                if let Ok(mut capture) = timeout_state.capture.lock() {
                    capture.fail("maximum recording duration reached");
                }
                timeout_state.emit("recording.changed", json!({"phase": "failed", "session_id": id, "generation": generation, "message": "maximum recording duration reached"}));
            }
        });
        Ok(json!({"session_id": id, "generation": generation, "state": "capturing"}))
    }

    async fn record_stop(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["wait"])?;
        let wait = params.get("wait").and_then(Value::as_bool).unwrap_or(false);
        let (generation, session_id, mode, state) = {
            let mut capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            let generation = capture.stop().map_err(conflict)?;
            let (session_id, mode) = match capture.state() {
                openwhisper_core::CaptureState::Transcribing {
                    session_id, mode, ..
                } => (*session_id, *mode),
                _ => unreachable!(),
            };
            let state = serde_json::to_value(capture.state()).map_err(internal)?;
            (generation, session_id, mode, state)
        };
        self.emit("recording.changed", state);

        #[cfg(any(test, feature = "test-capture"))]
        if self.test_capture {
            return Ok(
                json!({"session_id": session_id, "generation": generation, "state": "transcribing", "accepted": true}),
            );
        }
        let recorder = self
            .runtime
            .lock()
            .await
            .recorder
            .take()
            .ok_or_else(|| internal("active recorder is missing"))?;
        let audio_path = recorder.stop().await.map_err(audio_rpc_error)?;
        let copy = self
            .config
            .read()
            .map_err(|_| internal("config state is poisoned"))?
            .delivery
            .clipboard;
        let state = self.clone();
        let task = tokio::spawn(async move {
            state
                .process_recording(
                    session_id,
                    generation,
                    mode,
                    audio_path,
                    TranscriptionSource::Microphone,
                    copy,
                    None,
                )
                .await;
        });
        if let Some(previous) = self
            .processing
            .lock()
            .map_err(|_| internal("processing task is poisoned"))?
            .replace(task)
        {
            previous.abort();
        }
        if wait {
            return self
                .wait_for_result(session_id)
                .await
                .map(|result| serde_json::to_value(result).expect("result serializes"));
        }
        Ok(
            json!({"session_id": session_id, "generation": generation, "state": "transcribing", "accepted": true}),
        )
    }

    async fn record_toggle(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["mode"])?;
        let phase = self
            .capture
            .lock()
            .map_err(|_| internal("capture state is poisoned"))?
            .state()
            .clone();
        if matches!(phase, openwhisper_core::CaptureState::Capturing { .. }) {
            self.record_stop(json!({"wait": false})).await
        } else {
            self.record_start(params).await
        }
    }

    async fn record_cancel(&self) -> Result<Value, RpcError> {
        {
            let mut capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            capture.cancel().map_err(conflict)?;
        }
        if let Some(task) = self
            .processing
            .lock()
            .map_err(|_| internal("processing task is poisoned"))?
            .take()
        {
            task.abort();
        }
        let mut runtime = self.runtime.lock().await;
        if let Some(recorder) = runtime.recorder.take() {
            recorder.cancel().await;
        }
        if let Some(worker) = runtime.worker.as_mut() {
            let _ = worker.restart().await;
        }
        drop(runtime);
        self.emit("recording.changed", json!({"phase": "idle"}));
        Ok(json!({"cancelled": true}))
    }

    fn record_result(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["session_id"])?;
        let session_id = uuid_param(&params, "session_id")?;
        self.prune_results();
        let results = self
            .results
            .lock()
            .map_err(|_| internal("result cache is poisoned"))?;
        if let Some(error) = self
            .result_errors
            .lock()
            .map_err(|_| internal("result error cache is poisoned"))?
            .get(&session_id)
            .map(|(_, error)| error.clone())
        {
            return Err(error);
        }
        results
            .get(&session_id)
            .map(|cached| serde_json::to_value(&cached.result).expect("result serializes"))
            .ok_or_else(|| {
                RpcError::new(
                    ErrorCode::Configuration,
                    "transcription result is unavailable or expired",
                )
            })
    }

    async fn start_recorder(&self) -> Result<ActiveCapture, RpcError> {
        let blockers = self.readiness_blockers();
        if !blockers.is_empty() {
            let first = &blockers[0];
            return Err(RpcError::new(
                if first.capability == "model" {
                    ErrorCode::ModelUnavailable
                } else {
                    ErrorCode::UnsupportedCapability
                },
                first.detail.clone(),
            )
            .with_action(first.action.clone()));
        }
        let config = self
            .config
            .read()
            .map_err(|_| internal("config state is poisoned"))?
            .audio
            .clone();
        ActiveCapture::start(&self.paths.session_dir(), &config)
            .await
            .map_err(audio_rpc_error)
    }

    async fn process_recording(
        &self,
        session_id: Uuid,
        generation: u64,
        mode: Mode,
        audio_path: PathBuf,
        source: TranscriptionSource,
        copy: bool,
        language: Option<String>,
    ) {
        let outcome = self
            .transcribe_and_deliver(
                session_id,
                generation,
                mode,
                &audio_path,
                source,
                copy,
                language,
            )
            .await;
        if let Err(error) = outcome {
            if let Ok(mut capture) = self.capture.lock() {
                if capture.sequence() == generation {
                    capture.fail(error.message.clone());
                }
            }
            self.emit("recording.changed", json!({"phase": "failed", "session_id": session_id, "generation": generation, "message": error.message.clone()}));
            if let Ok(mut errors) = self.result_errors.lock() {
                errors.insert(session_id, (Instant::now(), error));
            }
            if let Ok(mut waiters) = self.result_ready.lock()
                && let Some(notify) = waiters.remove(&session_id)
            {
                notify.notify_waiters();
            }
        }
        if let Some(directory) = audio_path.parent() {
            let _ = tokio::fs::remove_dir_all(directory).await;
        }
    }

    async fn transcribe_and_deliver(
        &self,
        session_id: Uuid,
        generation: u64,
        mode: Mode,
        audio_path: &std::path::Path,
        source: TranscriptionSource,
        copy: bool,
        language: Option<String>,
    ) -> Result<(), RpcError> {
        let config = self
            .config
            .read()
            .map_err(|_| internal("config state is poisoned"))?
            .clone();
        let installed = self
            .store
            .installed_model(&config.model.selected)
            .map_err(internal)?
            .ok_or_else(|| {
                RpcError::new(
                    ErrorCode::ModelUnavailable,
                    "the selected verified model is not installed",
                )
            })?;
        let manifest = self.manifest(&config.model.selected)?;
        if !self.registered_model_is_ready(&manifest, &installed) {
            return Err(RpcError::new(
                ErrorCode::ModelUnavailable,
                "the selected model failed pinned size, hash, path, or ABI verification",
            )
            .with_action("Run `openwhisper models verify balanced`, then reinstall or import the exact pinned artifact."));
        }
        let request = WorkerRequest {
            id: Uuid::new_v4(),
            generation,
            command: WorkerCommand::Transcribe {
                model_path: installed.path,
                audio_path: audio_path.to_string_lossy().into_owned(),
                language: language.unwrap_or_else(|| config.language.clone()),
            },
        };
        let response = {
            let mut runtime = self.runtime.lock().await;
            if runtime.worker.is_none() {
                runtime.worker = Some(
                    WorkerSupervisor::spawn_with_threads(
                        self.paths.worker_executable(),
                        config.model.threads,
                    )
                    .await
                    .map_err(worker_rpc_error)?,
                );
            }
            let worker = runtime.worker.as_mut().expect("worker initialized");
            match worker.request(&request, Duration::from_secs(300)).await {
                Err(SupervisorError::Crashed) | Err(SupervisorError::Io(_)) => {
                    worker.restart().await.map_err(worker_rpc_error)?;
                    worker
                        .request(&request, Duration::from_secs(300))
                        .await
                        .map_err(worker_rpc_error)?
                }
                result => result.map_err(worker_rpc_error)?,
            }
        };
        let (raw_text, detected_language) = match response {
            WorkerResponse::Transcript {
                id,
                generation: response_generation,
                text,
                language,
            } if id == request.id && response_generation == generation => (text, language),
            WorkerResponse::Error { code, message, .. } => {
                let mut error = RpcError::new(ErrorCode::TranscriptionFailed, message);
                error.detail = Some(code);
                return Err(error);
            }
            WorkerResponse::Cancelled { .. } => {
                return Err(RpcError::new(
                    ErrorCode::Cancelled,
                    "transcription was cancelled",
                ));
            }
            _ => {
                return Err(RpcError::new(
                    ErrorCode::Protocol,
                    "worker returned an unexpected response",
                ));
            }
        };
        if raw_text.trim().is_empty() {
            return Err(RpcError::new(
                ErrorCode::TranscriptionFailed,
                "transcription returned no text",
            ));
        }
        {
            let mut capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            capture.begin_processing(generation).map_err(conflict)?;
            self.emit(
                "recording.changed",
                serde_json::to_value(capture.state()).map_err(internal)?,
            );
        }
        let replacements = self
            .store
            .list_strings("replacements")
            .map_err(internal)?
            .into_iter()
            .filter_map(|(from, to)| to.map(|to| (from, to)))
            .collect();
        let final_text = TextProcessor::with_replacements(replacements).process(&raw_text, mode);
        {
            let mut capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            capture.begin_delivery(generation).map_err(conflict)?;
            self.emit(
                "recording.changed",
                serde_json::to_value(capture.state()).map_err(internal)?,
            );
        }
        let duration_ms = std::fs::metadata(audio_path)
            .map(|meta| meta.len().saturating_sub(44) * 1000 / 32_000)
            .unwrap_or(0);
        let mut warnings = Vec::new();
        let history_id = if config.history.enabled {
            match self.store.add_history(HistoryInput {
                raw_text: raw_text.clone(),
                final_text: final_text.clone(),
                mode,
                language: detected_language.clone(),
                duration_ms,
                inserted: false,
                source: source_name(source).into(),
            }) {
                Ok(entry) => Some(entry.id),
                Err(error) => {
                    warnings.push(format!("History was not saved: {error}"));
                    None
                }
            }
        } else {
            None
        };
        let copied = if copy {
            match openwhisper_core::clipboard::copy_text(&final_text).await {
                Ok(_) => true,
                Err(error) => {
                    warnings.push(format!("Clipboard delivery failed: {error}. Copy the displayed transcript manually."));
                    false
                }
            }
        } else {
            false
        };
        let result = TranscriptionResult {
            session_id,
            generation,
            raw_text,
            final_text,
            language: protocol_language(&detected_language),
            mode: protocol_mode(mode),
            duration_ms,
            source,
            history_id,
            inserted: false,
            copied,
            insertion_method: "clipboard".into(),
            warnings,
        };
        {
            let mut capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            if !capture.complete(generation) {
                return Err(RpcError::new(
                    ErrorCode::Cancelled,
                    "stale transcription result was discarded",
                ));
            }
        }
        self.prune_results();
        self.results
            .lock()
            .map_err(|_| internal("result cache is poisoned"))?
            .insert(
                session_id,
                CachedResult {
                    completed_at: Instant::now(),
                    result,
                },
            );
        if let Some(notify) = self
            .result_ready
            .lock()
            .map_err(|_| internal("result waiter is poisoned"))?
            .remove(&session_id)
        {
            notify.notify_waiters();
        }
        self.emit(
            "result.available",
            json!({"session_id": session_id, "generation": generation}),
        );
        self.emit("recording.changed", json!({"phase": "idle"}));
        Ok(())
    }

    async fn transcribe_file(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(
            &params,
            &["path", "mode", "language", "copy", "insert", "source"],
        )?;
        if params.get("insert").and_then(Value::as_bool) == Some(true) {
            return Err(unsupported(
                "automatic insertion is outside the Linux dictation slice",
                "Use `--copy` or copy the printed transcript.",
            ));
        }
        let path = std::fs::canonicalize(string_param(&params, "path")?).map_err(|error| {
            RpcError::new(ErrorCode::Io, format!("input could not be opened: {error}"))
        })?;
        if !self.paths.worker_executable().is_file() {
            return Err(RpcError::new(
                ErrorCode::ModelUnavailable,
                "the native transcription worker is unavailable",
            ));
        }
        let selected = self
            .config
            .read()
            .map_err(|_| internal("config state is poisoned"))?
            .model
            .selected
            .clone();
        if self
            .store
            .installed_model(&selected)
            .map_err(internal)?
            .is_none()
        {
            return Err(RpcError::new(ErrorCode::ModelUnavailable, "no verified transcription model is installed")
                .with_action("Run `openwhisper models install balanced` or import the exact built-in pinned artifact."));
        }
        let mode = params
            .get("mode")
            .and_then(Value::as_str)
            .map(parse_mode)
            .transpose()?
            .unwrap_or(Mode::Raw);
        let language = params.get("language").and_then(Value::as_str);
        if let Some(language) = language {
            if !matches!(language, "auto" | "ar" | "en") {
                return Err(RpcError::new(
                    ErrorCode::Configuration,
                    "language must be auto, ar, or en",
                ));
            }
        }
        let session_id = Uuid::new_v4();
        let directory = self.paths.session_dir().join(session_id.to_string());
        tokio::fs::create_dir_all(&directory)
            .await
            .map_err(internal)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            tokio::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700))
                .await
                .map_err(internal)?;
        }
        let staged = directory.join("input.wav");
        tokio::fs::copy(&path, &staged).await.map_err(internal)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            tokio::fs::set_permissions(&staged, std::fs::Permissions::from_mode(0o600))
                .await
                .map_err(internal)?;
        }
        let transition = (|| {
            let mut capture = self
                .capture
                .lock()
                .map_err(|_| internal("capture state is poisoned"))?;
            capture
                .start_session(session_id, mode, None)
                .map_err(conflict)?;
            capture.stop().map_err(conflict)
        })();
        let generation = match transition {
            Ok(generation) => generation,
            Err(error) => {
                let _ = tokio::fs::remove_dir_all(&directory).await;
                return Err(error);
            }
        };
        self.emit("recording.changed", json!({"phase": "transcribing", "session_id": session_id, "generation": generation, "mode": mode}));
        let source = if params.get("source").and_then(Value::as_str) == Some("stdin") {
            TranscriptionSource::Stdin
        } else {
            TranscriptionSource::File
        };
        self.process_recording(
            session_id,
            generation,
            mode,
            staged,
            source,
            params.get("copy").and_then(Value::as_bool).unwrap_or(false),
            language.map(str::to_owned),
        )
        .await;
        self.record_result(json!({"session_id": session_id}))
    }

    async fn wait_for_result(&self, session_id: Uuid) -> Result<TranscriptionResult, RpcError> {
        if let Some(error) = self.cached_result_error(session_id)? {
            return Err(error);
        }
        if let Some(result) = self
            .results
            .lock()
            .map_err(|_| internal("result cache is poisoned"))?
            .get(&session_id)
            .cloned()
        {
            return Ok(result.result);
        }
        if let Some(error) = self.cached_result_error(session_id)? {
            return Err(error);
        }
        let notify = {
            let mut waiters = self
                .result_ready
                .lock()
                .map_err(|_| internal("result waiter is poisoned"))?;
            waiters
                .entry(session_id)
                .or_insert_with(|| Arc::new(Notify::new()))
                .clone()
        };
        if let Some(result) = self
            .results
            .lock()
            .map_err(|_| internal("result cache is poisoned"))?
            .get(&session_id)
            .cloned()
        {
            return Ok(result.result);
        }
        tokio::time::timeout(Duration::from_secs(310), notify.notified())
            .await
            .map_err(|_| {
                RpcError::new(
                    ErrorCode::TranscriptionFailed,
                    "transcription wait timed out",
                )
            })?;
        if let Some(error) = self.cached_result_error(session_id)? {
            return Err(error);
        }
        self.results
            .lock()
            .map_err(|_| internal("result cache is poisoned"))?
            .get(&session_id)
            .map(|cached| cached.result.clone())
            .ok_or_else(|| {
                RpcError::new(
                    ErrorCode::TranscriptionFailed,
                    "transcription did not produce a result",
                )
            })
    }

    fn prune_results(&self) {
        if let Ok(mut results) = self.results.lock() {
            results.retain(|_, result| result.completed_at.elapsed() <= Duration::from_secs(60));
        }
        if let Ok(mut errors) = self.result_errors.lock() {
            errors.retain(|_, (completed_at, _)| completed_at.elapsed() <= Duration::from_secs(60));
        }
    }

    fn cached_result_error(&self, session_id: Uuid) -> Result<Option<RpcError>, RpcError> {
        Ok(self
            .result_errors
            .lock()
            .map_err(|_| internal("result error cache is poisoned"))?
            .get(&session_id)
            .map(|(_, error)| error.clone()))
    }

    fn manifest(&self, name: &str) -> Result<BuiltinModelManifest, RpcError> {
        if name != openwhisper_core::models::BUILTIN_MODEL_NAME {
            return Err(RpcError::new(
                ErrorCode::ModelUnavailable,
                format!("unknown built-in model profile: {name}"),
            )
            .with_action("Use `openwhisper models list`; only `balanced` is installable in this source build."));
        }
        let manifest = builtin_balanced_model();
        manifest
            .artifact()
            .validate_for_abi(WORKER_ABI)
            .map_err(model_rpc_error)?;
        Ok(manifest)
    }

    fn model_list(&self) -> Result<Vec<ModelInfo>, RpcError> {
        let manifest = builtin_balanced_model();
        let installed = self
            .store
            .installed_model(&manifest.name)
            .map_err(internal)?;
        let is_ready = installed
            .as_ref()
            .is_some_and(|model| self.registered_model_is_ready(&manifest, model));
        let selected = self
            .config
            .read()
            .map_err(|_| internal("config state is poisoned"))?
            .model
            .selected
            == manifest.name;
        let installing = self
            .model_progress
            .lock()
            .map_err(|_| internal("model progress state is poisoned"))?
            .is_some();
        Ok(vec![ModelInfo {
            name: manifest.name,
            model_id: manifest.model_id,
            installed: is_ready,
            selected,
            installing,
            trust: ModelTrust::BuiltinPinned,
            benchmark_status: BenchmarkStatus::NotRun,
            source: manifest.source,
            license: manifest.license,
            size_bytes: manifest.size_bytes,
            sha256: manifest.sha256,
            worker_abi: manifest.worker_abi,
            path: installed.filter(|_| is_ready).map(|model| model.path),
        }])
    }

    async fn model_install(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["name", "yes"])?;
        let name = string_param(&params, "name")?;
        if params.get("yes").and_then(Value::as_bool) != Some(true) {
            return Err(RpcError::new(
                ErrorCode::Usage,
                "model installation requires confirmation",
            )
            .with_action("Review `openwhisper models list`, then rerun with `--yes` or confirm interactively."));
        }
        let manifest = self.manifest(name)?;
        let _operation = self.model_install_lock.lock().await;
        self.ensure_model_operation_idle()?;
        let initial = ModelDownloadProgress {
            name: manifest.name.clone(),
            downloaded_bytes: 0,
            total_bytes: manifest.size_bytes,
        };
        *self
            .model_progress
            .lock()
            .map_err(|_| internal("model progress state is poisoned"))? = Some(initial.clone());
        self.emit("model.download.progress", json!(initial));

        let mut last_emitted = 0_u64;
        let state = self.clone();
        let outcome = download_model(
            &manifest.artifact(),
            &manifest.source,
            &self.paths.model_dir(),
            DownloadOptions::default(),
            move |downloaded_bytes, total_bytes| {
                if downloaded_bytes == total_bytes
                    || downloaded_bytes == 0
                    || downloaded_bytes.saturating_sub(last_emitted) >= 1024 * 1024
                {
                    last_emitted = downloaded_bytes;
                    let progress = ModelDownloadProgress {
                        name: openwhisper_core::models::BUILTIN_MODEL_NAME.into(),
                        downloaded_bytes,
                        total_bytes,
                    };
                    if let Ok(mut current) = state.model_progress.lock() {
                        *current = Some(progress.clone());
                    }
                    state.emit("model.download.progress", json!(progress));
                }
            },
        )
        .await;
        *self
            .model_progress
            .lock()
            .map_err(|_| internal("model progress state is poisoned"))? = None;
        let outcome = match outcome {
            Ok(outcome) => outcome,
            Err(error) => {
                self.emit(
                    "model.install.failed",
                    json!({"name": manifest.name, "error": error.to_string()}),
                );
                return Err(model_rpc_error(error));
            }
        };
        let installed = self.register_model(&manifest, outcome.path.clone())?;
        self.emit(
            "model.installed",
            json!({"name": manifest.name, "path": installed.path, "reused": outcome.reused}),
        );
        Ok(json!({
            "name": installed.name,
            "installed": true,
            "reused": outcome.reused,
            "path": installed.path,
            "size_bytes": installed.size_bytes,
            "sha256": installed.sha256,
            "worker_abi": installed.worker_abi,
        }))
    }

    async fn model_import(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["name", "path"])?;
        let manifest = self.manifest(string_param(&params, "name")?)?;
        let source = PathBuf::from(string_param(&params, "path")?);
        let _operation = self.model_install_lock.lock().await;
        self.ensure_model_operation_idle()?;
        let artifact = manifest.artifact();
        artifact
            .validate_for_abi(WORKER_ABI)
            .map_err(model_rpc_error)?;
        let destination = artifact
            .import_offline(&source, &self.paths.model_dir())
            .map_err(model_rpc_error)?;
        let installed = self.register_model(&manifest, destination)?;
        self.emit(
            "model.installed",
            json!({"name": installed.name, "offline": true}),
        );
        Ok(json!({
            "name": installed.name,
            "installed": true,
            "offline": true,
            "path": installed.path,
            "size_bytes": installed.size_bytes,
            "sha256": installed.sha256,
            "worker_abi": installed.worker_abi,
        }))
    }

    fn register_model(
        &self,
        manifest: &BuiltinModelManifest,
        path: PathBuf,
    ) -> Result<InstalledModel, RpcError> {
        let expected = manifest.artifact().canonical_path(&self.paths.model_dir());
        if path != expected {
            return Err(model_rpc_error(ModelError::UnsafePath));
        }
        verify_file(&manifest.artifact(), &path).map_err(model_rpc_error)?;
        let installed = InstalledModel {
            name: manifest.name.clone(),
            model_id: manifest.model_id.clone(),
            path: path.to_string_lossy().into_owned(),
            sha256: manifest.sha256.clone(),
            size_bytes: manifest.size_bytes,
            worker_abi: manifest.worker_abi.clone(),
            installed_at: chrono::Utc::now(),
        };
        self.store
            .put_installed_model(&installed)
            .map_err(internal)?;
        self.cache_verified_fingerprint(&installed.name, &path);
        Ok(installed)
    }

    fn model_verify(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["name"])?;
        self.ensure_model_operation_idle()?;
        let manifest = self.manifest(string_param(&params, "name")?)?;
        let model = self
            .store
            .installed_model(&manifest.name)
            .map_err(internal)?
            .ok_or_else(|| {
                RpcError::new(ErrorCode::ModelUnavailable, "the model is not installed")
                    .with_action(
                        "Run `openwhisper models install balanced` or import the pinned artifact.",
                    )
            })?;
        if self
            .registered_model_matches_manifest(&manifest, &model)
            .is_err()
        {
            self.store
                .remove_installed_model(&manifest.name)
                .map_err(internal)?;
            return Err(model_rpc_error(ModelError::AbiMismatch));
        }
        let path = PathBuf::from(&model.path);
        match verify_file(&manifest.artifact(), &path) {
            Ok(()) => {
                self.cache_verified_fingerprint(&manifest.name, &path);
                Ok(json!({"name": manifest.name, "verified": true, "path": model.path}))
            }
            Err(error) => {
                if path.parent() == Some(self.paths.model_dir().as_path()) && path.exists() {
                    let _ = quarantine_file(&path, &self.paths.model_dir(), &manifest.model_id);
                }
                self.store
                    .remove_installed_model(&manifest.name)
                    .map_err(internal)?;
                if let Ok(mut cache) = self.verified_model_fingerprints.lock() {
                    cache.remove(&manifest.name);
                }
                Err(model_rpc_error(error))
            }
        }
    }

    async fn model_remove(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["name"])?;
        let manifest = self.manifest(string_param(&params, "name")?)?;
        let _operation = self.model_install_lock.lock().await;
        self.ensure_model_operation_idle()?;
        let expected = manifest.artifact().canonical_path(&self.paths.model_dir());
        if let Some(model) = self
            .store
            .installed_model(&manifest.name)
            .map_err(internal)?
        {
            if PathBuf::from(&model.path) != expected {
                return Err(model_rpc_error(ModelError::UnsafePath));
            }
        }
        if expected.exists() {
            let metadata = std::fs::symlink_metadata(&expected).map_err(internal)?;
            if metadata.file_type().is_symlink()
                || expected.parent() != Some(self.paths.model_dir().as_path())
            {
                return Err(model_rpc_error(ModelError::UnsafePath));
            }
            std::fs::remove_file(&expected).map_err(internal)?;
            File::open(self.paths.model_dir())
                .and_then(|directory| directory.sync_all())
                .map_err(internal)?;
        }
        let registered = self
            .store
            .remove_installed_model(&manifest.name)
            .map_err(internal)?;
        if let Ok(mut cache) = self.verified_model_fingerprints.lock() {
            cache.remove(&manifest.name);
        }
        self.emit("model.removed", json!({"name": manifest.name}));
        Ok(json!({"name": manifest.name, "removed": registered || !expected.exists()}))
    }

    fn model_select(&self, params: Value) -> Result<Value, RpcError> {
        reject_unknown_params(&params, &["name"])?;
        let name = string_param(&params, "name")?;
        self.ensure_model_operation_idle()?;
        let manifest = self.manifest(name)?;
        let installed = self.store.installed_model(name).map_err(internal)?;
        if !installed
            .as_ref()
            .is_some_and(|model| self.registered_model_is_ready(&manifest, model))
        {
            return Err(RpcError::new(
                ErrorCode::ModelUnavailable,
                "the selected model is not installed and verified",
            )
            .with_action("Run `openwhisper models install balanced`, then select it."));
        }
        self.update_config(|config| config.model.selected = name.into())?;
        self.emit("config.changed", json!({"model.selected": name}));
        Ok(json!({"selected": name}))
    }

    fn registered_model_matches_manifest(
        &self,
        manifest: &BuiltinModelManifest,
        model: &InstalledModel,
    ) -> Result<PathBuf, ModelError> {
        let expected = manifest.artifact().canonical_path(&self.paths.model_dir());
        let path = PathBuf::from(&model.path);
        if path != expected || path.parent() != Some(self.paths.model_dir().as_path()) {
            return Err(ModelError::UnsafePath);
        }
        if model.model_id != manifest.model_id
            || model.sha256 != manifest.sha256
            || model.size_bytes != manifest.size_bytes
            || model.worker_abi != manifest.worker_abi
            || model.worker_abi != WORKER_ABI
        {
            return Err(ModelError::AbiMismatch);
        }
        let metadata = std::fs::symlink_metadata(&path)?;
        if metadata.file_type().is_symlink()
            || !metadata.is_file()
            || metadata.len() != manifest.size_bytes
        {
            return Err(ModelError::SizeMismatch);
        }
        Ok(path)
    }

    fn registered_model_is_ready(
        &self,
        manifest: &BuiltinModelManifest,
        model: &InstalledModel,
    ) -> bool {
        let Ok(path) = self.registered_model_matches_manifest(manifest, model) else {
            return false;
        };
        let Ok(metadata) = path.metadata() else {
            return false;
        };
        let fingerprint = (metadata.len(), metadata.modified().ok());
        if self
            .verified_model_fingerprints
            .lock()
            .ok()
            .and_then(|cache| cache.get(&manifest.name).cloned())
            .as_ref()
            == Some(&fingerprint)
        {
            return true;
        }
        if verify_file(&manifest.artifact(), &path).is_err() {
            return false;
        }
        if let Ok(mut cache) = self.verified_model_fingerprints.lock() {
            cache.insert(manifest.name.clone(), fingerprint);
        }
        true
    }

    fn cache_verified_fingerprint(&self, name: &str, path: &std::path::Path) {
        if let Ok(metadata) = path.metadata()
            && let Ok(mut cache) = self.verified_model_fingerprints.lock()
        {
            cache.insert(name.into(), (metadata.len(), metadata.modified().ok()));
        }
    }

    fn ensure_model_operation_idle(&self) -> Result<(), RpcError> {
        let capture = self
            .capture
            .lock()
            .map_err(|_| internal("capture state is poisoned"))?;
        if matches!(
            capture.state(),
            openwhisper_core::CaptureState::Idle | openwhisper_core::CaptureState::Failed { .. }
        ) {
            Ok(())
        } else {
            Err(RpcError::new(
                ErrorCode::Conflict,
                "model operations are disabled while capture or transcription is active",
            )
            .with_action("Finish or cancel the active capture, then retry the model operation."))
        }
    }

    fn readiness_blockers(&self) -> Vec<openwhisper_protocol::ReadinessBlocker> {
        #[cfg(any(test, feature = "test-capture"))]
        if self.test_capture {
            return Vec::new();
        }
        let mut blockers = Vec::new();
        let capabilities = detect_capabilities();
        if !capabilities.audio.available {
            blockers.push(openwhisper_protocol::ReadinessBlocker {
                capability: "audio".into(),
                code: "audio_backend_unavailable".into(),
                detail: capabilities.audio.detail,
                action: capabilities
                    .audio
                    .fallback
                    .unwrap_or_else(|| "Install PipeWire, PulseAudio, or ALSA tools.".into()),
            });
        }
        if !self.paths.worker_executable().is_file() {
            blockers.push(openwhisper_protocol::ReadinessBlocker { capability: "worker".into(), code: "worker_unavailable".into(), detail: "The persistent native worker executable is missing.".into(), action: "Install the complete OpenWhisper package containing openwhisper, openwhisperd, and openwhisper-worker-native.".into() });
        }
        if self
            .model_progress
            .lock()
            .ok()
            .is_some_and(|progress| progress.is_some())
        {
            blockers.push(openwhisper_protocol::ReadinessBlocker {
                capability: "model".into(),
                code: "model_installing".into(),
                detail: "The balanced model is currently downloading or being verified.".into(),
                action: "Wait for model installation to complete; capture remains disabled during registration.".into(),
            });
        }
        let selected = self
            .config
            .read()
            .ok()
            .map(|config| config.model.selected.clone())
            .unwrap_or_else(|| "balanced".into());
        let manifest = builtin_balanced_model();
        match self.store.installed_model(&selected) {
            Ok(Some(model))
                if selected == manifest.name
                    && self.registered_model_is_ready(&manifest, &model) => {}
            _ => blockers.push(openwhisper_protocol::ReadinessBlocker {
                capability: "model".into(),
                code: "verified_model_unavailable".into(),
                detail: "No built-in pinned, verified model is installed for the selected profile."
                    .into(),
                action:
                    "Run `openwhisper models install balanced` or import the exact pinned artifact."
                        .into(),
            }),
        }
        blockers
    }

    pub async fn cleanup_runtime_audio(&self) -> Result<(), RpcError> {
        cleanup_stale_sessions(&self.paths.session_dir())
            .await
            .map_err(audio_rpc_error)
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
                if !matches!(language.as_str(), "auto" | "ar" | "en") {
                    return Err(RpcError::new(
                        ErrorCode::Configuration,
                        "language must be auto, ar, or en",
                    ));
                }
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
            "audio.backend" => {
                let backend = match value.as_str() {
                    Some("auto") => openwhisper_core::AudioBackend::Auto,
                    Some("pipewire") => openwhisper_core::AudioBackend::Pipewire,
                    Some("pulse") => openwhisper_core::AudioBackend::Pulse,
                    Some("alsa") => openwhisper_core::AudioBackend::Alsa,
                    _ => {
                        return Err(RpcError::new(
                            ErrorCode::Configuration,
                            "audio.backend must be auto, pipewire, pulse, or alsa",
                        ));
                    }
                };
                self.update_config(|config| config.audio.backend = backend)?;
            }
            "audio.device" => {
                let device = if let Some(device) = value.as_str() {
                    device.to_owned()
                } else if let Some(node_id) = value.as_u64() {
                    node_id.to_string()
                } else {
                    return Err(RpcError::new(
                        ErrorCode::Configuration,
                        "audio.device must be a device name or non-negative numeric node ID",
                    ));
                };
                self.update_config(|config| config.audio.device = device)?;
            }
            "audio.max_recording_seconds" => {
                let seconds = value
                    .as_u64()
                    .and_then(|value| u16::try_from(value).ok())
                    .filter(|value| (10..=600).contains(value))
                    .ok_or_else(|| {
                        RpcError::new(
                            ErrorCode::Configuration,
                            "audio.max_recording_seconds must be between 10 and 600",
                        )
                    })?;
                self.update_config(|config| config.audio.max_recording_seconds = seconds)?;
            }
            "model.selected" => {
                let selected = value
                    .as_str()
                    .ok_or_else(|| {
                        RpcError::new(ErrorCode::Configuration, "model.selected must be a string")
                    })?
                    .to_owned();
                self.update_config(|config| config.model.selected = selected)?;
            }
            "model.threads" => {
                let threads = value
                    .as_u64()
                    .and_then(|value| u16::try_from(value).ok())
                    .ok_or_else(|| {
                        RpcError::new(
                            ErrorCode::Configuration,
                            "model.threads must be an unsigned 16-bit integer",
                        )
                    })?;
                self.update_config(|config| config.model.threads = threads)?;
            }
            "delivery.clipboard" => {
                let clipboard = value.as_bool().ok_or_else(|| {
                    RpcError::new(
                        ErrorCode::Configuration,
                        "delivery.clipboard must be a boolean",
                    )
                })?;
                self.update_config(|config| config.delivery.clipboard = clipboard)?;
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

fn reject_unknown_params(params: &Value, allowed: &[&str]) -> Result<(), RpcError> {
    let Some(object) = params.as_object() else {
        if params.is_null() {
            return Ok(());
        }
        return Err(RpcError::new(
            ErrorCode::Usage,
            "parameters must be an object",
        ));
    };
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(RpcError::new(
            ErrorCode::Usage,
            format!("unknown parameter: {key}"),
        ));
    }
    Ok(())
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

fn audio_rpc_error(error: openwhisper_core::audio::AudioError) -> RpcError {
    use openwhisper_core::audio::AudioError;
    let code = match error {
        AudioError::PermissionDenied => ErrorCode::PermissionDenied,
        AudioError::BackendUnavailable | AudioError::Startup(_) => ErrorCode::UnsupportedCapability,
        AudioError::Empty | AudioError::Overflow | AudioError::EarlyExit => {
            ErrorCode::TranscriptionFailed
        }
        _ => ErrorCode::Io,
    };
    RpcError::new(code, error.to_string())
}

fn worker_rpc_error(error: SupervisorError) -> RpcError {
    let code = match error {
        SupervisorError::IncompatibleAbi(_) => ErrorCode::ModelUnavailable,
        SupervisorError::Timeout | SupervisorError::Crashed => ErrorCode::TranscriptionFailed,
        _ => ErrorCode::Io,
    };
    RpcError::new(code, error.to_string())
}

fn model_rpc_error(error: ModelError) -> RpcError {
    let code = match error {
        ModelError::Network(_) | ModelError::HttpStatus(_) => ErrorCode::Network,
        ModelError::ConfirmationRequired => ErrorCode::Usage,
        ModelError::InstallConflict => ErrorCode::Conflict,
        ModelError::Io(_) | ModelError::InsufficientDisk { .. } => ErrorCode::Io,
        _ => ErrorCode::ModelUnavailable,
    };
    let mut rpc = RpcError::new(code, error.to_string());
    rpc.retryable = matches!(code, ErrorCode::Network | ErrorCode::Io);
    rpc.action = Some(match code {
        ErrorCode::Network => "Check the network connection and rerun `openwhisper models install balanced`; a valid partial download will resume.".into(),
        ErrorCode::Io => "Free disk space or fix permissions, then retry; verified existing bytes are preserved when safe.".into(),
        _ => "Run `openwhisper models verify balanced`; reinstall or import the exact built-in pinned artifact if verification fails.".into(),
    });
    rpc
}

fn protocol_mode(mode: Mode) -> TranscriptMode {
    match mode {
        Mode::Raw => TranscriptMode::Raw,
        Mode::Clean => TranscriptMode::Clean,
        Mode::Code => TranscriptMode::Code,
    }
}

fn protocol_language(language: &str) -> Language {
    match language {
        "ar" => Language::Ar,
        "en" => Language::En,
        _ => Language::Auto,
    }
}

fn source_name(source: TranscriptionSource) -> &'static str {
    match source {
        TranscriptionSource::Microphone => "microphone",
        TranscriptionSource::File => "file",
        TranscriptionSource::Stdin => "stdin",
    }
}

fn provider_catalog(local_ready: bool) -> Value {
    json!([
        {"id": "local", "kind": "local", "enabled": local_ready, "network": false, "reason": if local_ready { "built-in pinned model verified" } else { "no verified model installed" }},
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
        let state = DaemonState::initialize(AppPaths::under(temp.path())).unwrap();
        (temp, state)
    }

    #[tokio::test]
    async fn dispatches_capture_and_config_contracts() {
        let (_temp, daemon) = daemon();
        daemon
            .dispatch("record.start", json!({"mode": "clean"}))
            .await
            .unwrap();
        assert!(matches!(
            *daemon.capture.lock().unwrap().state(),
            CaptureState::Capturing {
                mode: Mode::Clean,
                ..
            }
        ));
        assert!(daemon.dispatch("record.start", json!({})).await.is_err());
        daemon.dispatch("record.cancel", json!({})).await.unwrap();
        daemon
            .dispatch(
                "config.set",
                json!({"key": "history.retention_days", "value": 0}),
            )
            .await
            .unwrap();
        assert_eq!(
            daemon
                .dispatch("config.get", json!({"key": "history.retention_days"}))
                .await
                .unwrap(),
            json!(0)
        );
        daemon
            .dispatch("config.set", json!({"key": "audio.device", "value": 59}))
            .await
            .unwrap();
        assert_eq!(
            daemon
                .dispatch("config.get", json!({"key": "audio.device"}))
                .await
                .unwrap(),
            json!("59")
        );
    }

    #[tokio::test]
    async fn transcript_fixture_is_disabled_in_production() {
        let (_temp, daemon) = daemon();
        let error = daemon
            .dispatch("history.add_fixture", json!({"text": "secret"}))
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::UnsupportedCapability);
    }

    #[tokio::test]
    async fn unknown_methods_are_structured_usage_errors() {
        let (_temp, daemon) = daemon();
        let error = daemon
            .dispatch("meetings.start", Value::Null)
            .await
            .unwrap_err();
        assert_eq!(error.code.exit_code(), 2);
    }

    #[tokio::test]
    async fn history_clear_requires_explicit_confirmation() {
        let (_temp, daemon) = daemon();
        let error = daemon
            .dispatch("history.clear", json!({}))
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Usage);
        assert_eq!(
            daemon
                .dispatch("history.clear", json!({"confirmed": true}))
                .await
                .unwrap(),
            json!({"deleted": 0})
        );
    }

    #[tokio::test]
    async fn gated_commands_and_model_confirmation_return_structured_errors() {
        let (_temp, daemon) = daemon();
        for method in ["service.install", "vocab.import", "snippets.export"] {
            assert_eq!(
                daemon.dispatch(method, json!({})).await.unwrap_err().code,
                ErrorCode::UnsupportedCapability
            );
        }
        assert_eq!(
            daemon
                .dispatch("models.install", json!({"name": "balanced"}))
                .await
                .unwrap_err()
                .code,
            ErrorCode::Usage
        );
        let models = daemon.dispatch("models.list", json!({})).await.unwrap();
        assert_eq!(models[0]["name"], "balanced");
        assert_eq!(models[0]["size_bytes"], 574_041_195_u64);
        assert_eq!(models[0]["trust"], "builtin_pinned");
        assert_eq!(models[0]["benchmark_status"], "not_run");
        assert_eq!(
            daemon
                .dispatch("providers.configure", json!({}))
                .await
                .unwrap_err()
                .code,
            ErrorCode::ProviderUnavailable
        );
    }

    #[tokio::test]
    async fn model_removal_is_confined_to_the_canonical_model_file() {
        let (temp, daemon) = daemon();
        let manifest = builtin_balanced_model();
        let outside = temp.path().join("outside-model.bin");
        std::fs::write(&outside, b"keep").unwrap();
        daemon
            .store
            .put_installed_model(&InstalledModel {
                name: manifest.name.clone(),
                model_id: manifest.model_id.clone(),
                path: outside.to_string_lossy().into_owned(),
                sha256: manifest.sha256.clone(),
                size_bytes: manifest.size_bytes,
                worker_abi: manifest.worker_abi.clone(),
                installed_at: chrono::Utc::now(),
            })
            .unwrap();
        let error = daemon
            .dispatch("models.remove", json!({"name": "balanced"}))
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::ModelUnavailable);
        assert_eq!(std::fs::read(&outside).unwrap(), b"keep");

        let canonical = manifest
            .artifact()
            .canonical_path(&daemon.paths.model_dir());
        std::fs::create_dir_all(daemon.paths.model_dir()).unwrap();
        std::fs::write(&canonical, b"canonical fixture").unwrap();
        daemon
            .store
            .put_installed_model(&InstalledModel {
                name: manifest.name.clone(),
                model_id: manifest.model_id,
                path: canonical.to_string_lossy().into_owned(),
                sha256: manifest.sha256,
                size_bytes: manifest.size_bytes,
                worker_abi: manifest.worker_abi,
                installed_at: chrono::Utc::now(),
            })
            .unwrap();
        daemon
            .dispatch("models.remove", json!({"name": "balanced"}))
            .await
            .unwrap();
        assert!(!canonical.exists());
        assert!(outside.exists());
    }

    #[tokio::test]
    async fn model_progress_is_typed_and_abi_mismatch_is_not_ready() {
        let (_temp, daemon) = daemon();
        let mut events = daemon.subscribe();
        let progress = ModelDownloadProgress {
            name: "balanced".into(),
            downloaded_bytes: 12,
            total_bytes: 24,
        };
        daemon.emit("model.download.progress", json!(progress));
        let ServerMessage::Event { event, data, .. } = events.recv().await.unwrap() else {
            panic!("expected progress event")
        };
        assert_eq!(event, "model.download.progress");
        assert_eq!(data["downloaded_bytes"], 12);

        let manifest = builtin_balanced_model();
        let path = manifest
            .artifact()
            .canonical_path(&daemon.paths.model_dir());
        let model = InstalledModel {
            name: manifest.name.clone(),
            model_id: manifest.model_id.clone(),
            path: path.to_string_lossy().into_owned(),
            sha256: manifest.sha256.clone(),
            size_bytes: manifest.size_bytes,
            worker_abi: "wrong-worker-abi".into(),
            installed_at: chrono::Utc::now(),
        };
        assert!(!daemon.registered_model_is_ready(&manifest, &model));
    }
}
