use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::io::{self, BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, SyncSender};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, Weak};
use std::thread;
use std::time::{Duration, Instant};
use uuid::Uuid;

const PROTOCOL_VERSION: u64 = 1;
const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
const IDLE_RESTART_DELAY: Duration = Duration::from_millis(500);
const RESTART_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);

type PendingReply = Result<Value, EngineError>;
type EventSink = dyn Fn(Value) + Send + Sync + 'static;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EngineCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
}

impl EngineCommand {
    pub fn resolve() -> Self {
        if let Some(program) = env::var_os("OPENWHISPER_ENGINE") {
            return Self {
                program: PathBuf::from(program),
                args: Vec::new(),
            };
        }

        if let Ok(current_exe) = env::current_exe() {
            if let Some(directory) = current_exe.parent() {
                let installed = directory.join("openwhisper-engine");
                if installed.is_file() {
                    return Self {
                        program: installed,
                        args: Vec::new(),
                    };
                }
            }
        }

        if cfg!(debug_assertions) {
            return Self {
                program: PathBuf::from("uv"),
                args: vec!["run".into(), "--frozen".into(), "openwhisper-engine".into()],
            };
        }

        Self {
            program: PathBuf::from("openwhisper-engine"),
            args: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineError {
    pub code: String,
    pub message: String,
}

impl EngineError {
    fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.to_owned(),
            message: message.to_owned(),
        }
    }

    fn unavailable() -> Self {
        Self::new("UNAVAILABLE", "The OpenWhisper engine is unavailable.")
    }

    fn internal() -> Self {
        Self::new(
            "INTERNAL",
            "The OpenWhisper host could not complete the request.",
        )
    }

    fn timeout() -> Self {
        Self::new(
            "UNAVAILABLE",
            "The OpenWhisper engine did not respond in time.",
        )
    }
}

struct ProcessState {
    generation: u64,
    child: Arc<Mutex<Child>>,
    stdin: Arc<Mutex<ChildStdin>>,
}

struct Lifecycle {
    process: Option<ProcessState>,
    generation: u64,
    starting: bool,
    restarting: bool,
    shutting_down: bool,
    fatal: bool,
    idle_restart_available: bool,
    dictation_state: String,
    last_event_sequence: u64,
}

pub struct EngineSupervisor {
    command: EngineCommand,
    lifecycle: Mutex<Lifecycle>,
    lifecycle_changed: Condvar,
    pending: Mutex<HashMap<String, SyncSender<PendingReply>>>,
    event_sink: Arc<EventSink>,
}

impl EngineSupervisor {
    pub fn new(command: EngineCommand, event_sink: Arc<EventSink>) -> Arc<Self> {
        Arc::new(Self {
            command,
            lifecycle: Mutex::new(Lifecycle {
                process: None,
                generation: 0,
                starting: false,
                restarting: false,
                shutting_down: false,
                fatal: false,
                idle_restart_available: true,
                dictation_state: "idle".into(),
                last_event_sequence: 0,
            }),
            lifecycle_changed: Condvar::new(),
            pending: Mutex::new(HashMap::new()),
            event_sink,
        })
    }

    pub fn start(self: &Arc<Self>) -> Result<(), EngineError> {
        self.ensure_started()
    }

    pub fn request(self: &Arc<Self>, method: &str, params: Value) -> Result<Value, EngineError> {
        let timeout = method_timeout(method).ok_or_else(|| {
            EngineError::new("NOT_FOUND", "The requested engine method is not available.")
        })?;
        if !params.is_object() {
            return Err(EngineError::new(
                "INVALID_ARGUMENT",
                "Engine request parameters must be an object.",
            ));
        }
        // Restarting is deliberately a host operation. It must never be
        // forwarded to the Python engine: doing so would let a provider or a
        // stale renderer tear down the process without the supervisor's
        // idle-state and re-handshake guarantees.
        if method == "app.restartEngine" {
            if !params.as_object().is_some_and(serde_json::Map::is_empty) {
                return Err(EngineError::new(
                    "INVALID_ARGUMENT",
                    "The engine restart operation takes no parameters.",
                ));
            }
            return self.restart_engine();
        }
        if method == "app.shutdown" {
            self.shutdown();
            return Ok(json!({"accepted": true}));
        }
        if method == "app.bootstrap" {
            let mut lifecycle = self.lock_lifecycle();
            if lifecycle.fatal {
                lifecycle.fatal = false;
                lifecycle.idle_restart_available = true;
            }
        }
        self.ensure_started()?;
        self.request_running(method, params, timeout)
    }

    pub fn dictation_state(&self) -> String {
        self.lock_lifecycle().dictation_state.clone()
    }

    /// Restart the child while preserving the host process and its event
    /// bridge. The returned bootstrap is from the new child, so callers can
    /// refresh settings, provider state, and the new engine session ID in one
    /// round trip.
    pub fn restart_engine(self: &Arc<Self>) -> Result<Value, EngineError> {
        let (child, generation) = {
            let mut lifecycle = self.lock_lifecycle();
            loop {
                if lifecycle.shutting_down {
                    return Err(EngineError::unavailable());
                }
                if lifecycle.starting || lifecycle.restarting {
                    lifecycle = self
                        .lifecycle_changed
                        .wait(lifecycle)
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    continue;
                }
                if is_active_state(&lifecycle.dictation_state) {
                    return Err(EngineError::new(
                        "BUSY",
                        "The engine cannot restart during dictation.",
                    ));
                }
                lifecycle.restarting = true;
                let generation = lifecycle.generation;
                let child = lifecycle
                    .process
                    .as_ref()
                    .map(|process| Arc::clone(&process.child));
                break (child, generation);
            }
        };

        // Ask the engine to close its Qt application first so audio, shortcut
        // registrations, and temporary files receive their normal cleanup.
        // A hung child is bounded and is killed below; restart must never
        // leave the UI waiting indefinitely.
        if child.is_some() {
            let _ = self.request_running("app.shutdown", json!({}), RESTART_SHUTDOWN_TIMEOUT);
        }
        if let Some(child) = child {
            wait_for_child_exit(&child, RESTART_SHUTDOWN_TIMEOUT);
        }

        {
            let mut lifecycle = self.lock_lifecycle();
            // The stdout thread may have observed the exit already. Only
            // clear a process belonging to this restart; a newer generation
            // is never disturbed.
            if lifecycle.process.as_ref().map(|process| process.generation) == Some(generation) {
                lifecycle.process = None;
            }
            lifecycle.restarting = false;
            lifecycle.fatal = false;
            lifecycle.idle_restart_available = true;
            lifecycle.dictation_state = "idle".into();
            lifecycle.last_event_sequence = 0;
            self.lifecycle_changed.notify_all();
        }
        self.fail_pending();

        self.ensure_started()?;
        // ensure_started performs the handshake before exposing the process.
        // Requesting bootstrap once more gives the renderer the complete,
        // post-restart state rather than only the protocol version check.
        self.request_running("app.bootstrap", json!({}), Duration::from_secs(5))
    }

    fn ensure_started(self: &Arc<Self>) -> Result<(), EngineError> {
        let generation = {
            let mut lifecycle = self.lock_lifecycle();
            loop {
                if lifecycle.shutting_down {
                    return Err(EngineError::unavailable());
                }
                if lifecycle.restarting {
                    lifecycle = self
                        .lifecycle_changed
                        .wait(lifecycle)
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    continue;
                }
                if lifecycle.fatal {
                    return Err(EngineError::unavailable());
                }
                if lifecycle.process.is_some() {
                    return Ok(());
                }
                if !lifecycle.starting {
                    lifecycle.starting = true;
                    lifecycle.generation += 1;
                    break lifecycle.generation;
                }
                lifecycle = self
                    .lifecycle_changed
                    .wait(lifecycle)
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
            }
        };

        let started = self.spawn_process(generation);
        let start_failed = started.is_err();
        {
            let mut lifecycle = self.lock_lifecycle();
            lifecycle.starting = false;
            if let Ok(process) = started {
                lifecycle.process = Some(process);
                // Event sequence numbers belong to an engine process, not to
                // the host. Resetting here lets a freshly handshaken child
                // emit sequence 1 while generation filtering drops stale
                // events from the previous child.
                lifecycle.last_event_sequence = 0;
                lifecycle.dictation_state = "idle".into();
            }
            self.lifecycle_changed.notify_all();
        }

        if start_failed {
            return Err(EngineError::unavailable());
        }

        let handshake = self.request_running("app.bootstrap", json!({}), Duration::from_secs(5));
        match handshake {
            Ok(result) if result.get("protocolVersion").and_then(Value::as_u64) == Some(1) => {
                Ok(())
            }
            Ok(_) => {
                self.terminate_generation(generation);
                Err(EngineError::new(
                    "PROTOCOL_MISMATCH",
                    "The OpenWhisper engine protocol is incompatible.",
                ))
            }
            Err(error) => {
                self.terminate_generation(generation);
                Err(error)
            }
        }
    }

    fn spawn_process(self: &Arc<Self>, generation: u64) -> Result<ProcessState, EngineError> {
        let mut command = Command::new(&self.command.program);
        command
            .args(&self.command.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|_| EngineError::unavailable())?;
        let stdin = child.stdin.take().ok_or_else(EngineError::unavailable)?;
        let stdout = child.stdout.take().ok_or_else(EngineError::unavailable)?;
        let stderr = child.stderr.take().ok_or_else(EngineError::unavailable)?;
        let child = Arc::new(Mutex::new(child));

        let weak = Arc::downgrade(self);
        thread::Builder::new()
            .name("openwhisper-engine-stdout".into())
            .spawn(move || read_stdout(weak, generation, stdout))
            .map_err(|_| EngineError::internal())?;

        thread::Builder::new()
            .name("openwhisper-engine-stderr".into())
            .spawn(move || {
                forward_engine_stderr(stderr, |safe| {
                    eprintln!("openwhisper-engine: {safe}");
                })
            })
            .map_err(|_| EngineError::internal())?;

        Ok(ProcessState {
            generation,
            child,
            stdin: Arc::new(Mutex::new(stdin)),
        })
    }

    fn request_running(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, EngineError> {
        let stdin = {
            let lifecycle = self.lock_lifecycle();
            lifecycle
                .process
                .as_ref()
                .map(|process| Arc::clone(&process.stdin))
                .ok_or_else(EngineError::unavailable)?
        };
        let id = Uuid::new_v4().to_string();
        let frame = serde_json::to_vec(&json!({
            "v": PROTOCOL_VERSION,
            "kind": "request",
            "id": id,
            "method": method,
            "params": params,
        }))
        .map_err(|_| EngineError::internal())?;
        if frame.len() > MAX_FRAME_BYTES {
            return Err(EngineError::new(
                "INVALID_ARGUMENT",
                "The engine request is too large.",
            ));
        }
        let (sender, receiver) = mpsc::sync_channel(1);
        self.lock_pending().insert(id.clone(), sender);

        let write_result = {
            let mut writer = stdin
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            writer
                .write_all(&frame)
                .and_then(|_| writer.write_all(b"\n"))
                .and_then(|_| writer.flush())
        };
        if write_result.is_err() {
            self.lock_pending().remove(&id);
            return Err(EngineError::unavailable());
        }

        match receiver.recv_timeout(timeout) {
            Ok(result) => result,
            Err(_) => {
                self.lock_pending().remove(&id);
                Err(EngineError::timeout())
            }
        }
    }

    fn accept_frame(self: &Arc<Self>, generation: u64, frame: Value) {
        // A process that exited during a manual restart can still have a few
        // bytes buffered in its stdout pipe. Never let those frames mutate
        // the new process' state or sequence cursor.
        if self
            .lock_lifecycle()
            .process
            .as_ref()
            .map(|process| process.generation)
            != Some(generation)
        {
            return;
        }
        match frame.get("kind").and_then(Value::as_str) {
            Some("response") => self.accept_response(frame),
            Some("event") => self.accept_event(frame),
            _ => {}
        }
    }

    fn accept_response(&self, frame: Value) {
        let Some(id) = frame.get("id").and_then(Value::as_str) else {
            return;
        };
        let Some(sender) = self.lock_pending().remove(id) else {
            return;
        };
        let reply = if frame.get("ok").and_then(Value::as_bool) == Some(true) {
            Ok(frame.get("result").cloned().unwrap_or(Value::Null))
        } else {
            let error = frame.get("error").and_then(Value::as_object);
            let code = error
                .and_then(|value| value.get("code"))
                .and_then(Value::as_str)
                .filter(|value| stable_error_code(value))
                .unwrap_or("INTERNAL");
            Err(EngineError::new(code, safe_error_message(code)))
        };
        let _ = sender.send(reply);
    }

    fn accept_event(&self, frame: Value) {
        if frame.get("v").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
            return;
        }
        let mut lifecycle = self.lock_lifecycle();
        let sequence = frame.get("seq").and_then(Value::as_u64).unwrap_or(0);
        if sequence <= lifecycle.last_event_sequence {
            return;
        }
        lifecycle.last_event_sequence = sequence;
        if frame.get("event").and_then(Value::as_str) == Some("dictation.state") {
            if let Some(state) = frame
                .get("payload")
                .and_then(|payload| payload.get("state"))
                .and_then(Value::as_str)
            {
                lifecycle.dictation_state = state.to_owned();
            }
        }
        drop(lifecycle);
        (self.event_sink)(frame);
    }

    fn on_exit(self: &Arc<Self>, generation: u64, reason: &str) {
        let (should_restart, should_report_fatal) = {
            let mut lifecycle = self.lock_lifecycle();
            if lifecycle.process.as_ref().map(|process| process.generation) != Some(generation) {
                return;
            }
            lifecycle.process = None;
            self.lifecycle_changed.notify_all();
            if lifecycle.shutting_down {
                (false, false)
            } else if lifecycle.restarting {
                // A manual restart owns this exit. It will clear the process,
                // reset the event cursor, and perform the new handshake.
                (false, false)
            } else if is_active_state(&lifecycle.dictation_state) {
                lifecycle.fatal = true;
                (false, true)
            } else if lifecycle.idle_restart_available {
                lifecycle.idle_restart_available = false;
                (true, false)
            } else {
                lifecycle.fatal = true;
                (false, true)
            }
        };
        self.fail_pending();
        if should_report_fatal {
            self.emit_fatal(reason);
        }
        if should_restart {
            let weak = Arc::downgrade(self);
            thread::spawn(move || {
                thread::sleep(IDLE_RESTART_DELAY);
                if let Some(supervisor) = weak.upgrade() {
                    if supervisor.ensure_started().is_err() {
                        supervisor.lock_lifecycle().fatal = true;
                        supervisor.emit_fatal("The engine could not be restarted.");
                    }
                }
            });
        }
    }

    fn emit_fatal(&self, message: &str) {
        let sequence = {
            let mut lifecycle = self.lock_lifecycle();
            lifecycle.last_event_sequence += 1;
            lifecycle.last_event_sequence
        };
        (self.event_sink)(json!({
            "v": PROTOCOL_VERSION,
            "kind": "event",
            "seq": sequence,
            "event": "engine.fatal",
            "payload": {"message": message},
        }));
    }

    fn terminate_generation(&self, generation: u64) {
        let child = {
            let mut lifecycle = self.lock_lifecycle();
            if lifecycle.process.as_ref().map(|process| process.generation) == Some(generation) {
                lifecycle.process.take().map(|process| process.child)
            } else {
                None
            }
        };
        if let Some(child) = child {
            let _ = child
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .kill();
        }
        self.fail_pending();
    }

    pub fn shutdown(&self) {
        let child = {
            let mut lifecycle = self.lock_lifecycle();
            if lifecycle.shutting_down {
                return;
            }
            lifecycle.shutting_down = true;
            lifecycle
                .process
                .as_ref()
                .map(|process| Arc::clone(&process.child))
        };
        let _ = self.request_running("app.shutdown", json!({}), Duration::from_secs(3));

        if let Some(child) = child {
            let deadline = Instant::now() + Duration::from_secs(3);
            loop {
                let exited = child
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner())
                    .try_wait()
                    .ok()
                    .flatten()
                    .is_some();
                if exited {
                    break;
                }
                if Instant::now() >= deadline {
                    let _ = child
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner())
                        .kill();
                    break;
                }
                thread::sleep(Duration::from_millis(50));
            }
        }
        self.fail_pending();
    }

    fn fail_pending(&self) {
        let pending = std::mem::take(&mut *self.lock_pending());
        for sender in pending.into_values() {
            let _ = sender.send(Err(EngineError::unavailable()));
        }
    }

    fn lock_lifecycle(&self) -> MutexGuard<'_, Lifecycle> {
        self.lifecycle
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn lock_pending(&self) -> MutexGuard<'_, HashMap<String, SyncSender<PendingReply>>> {
        self.pending
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

impl Drop for EngineSupervisor {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn read_stdout(weak: Weak<EngineSupervisor>, generation: u64, stdout: impl std::io::Read) {
    let mut reader = BufReader::new(stdout);
    let mut frame = Vec::new();
    let mut exit_reason = "The OpenWhisper engine stopped unexpectedly.";
    loop {
        frame.clear();
        match read_bounded_line(&mut reader, &mut frame) {
            Ok(0) => break,
            Ok(_) => {
                if frame.last() == Some(&b'\n') {
                    frame.pop();
                }
                if frame.last() == Some(&b'\r') {
                    frame.pop();
                }
                let Ok(value) = serde_json::from_slice::<Value>(&frame) else {
                    exit_reason = "The OpenWhisper engine sent an invalid frame.";
                    break;
                };
                if let Some(supervisor) = weak.upgrade() {
                    supervisor.accept_frame(generation, value);
                } else {
                    return;
                }
            }
            Err(_) => break,
        }
    }
    if let Some(supervisor) = weak.upgrade() {
        supervisor.on_exit(generation, exit_reason);
    }
}

fn wait_for_child_exit(child: &Arc<Mutex<Child>>, timeout: Duration) {
    let deadline = Instant::now() + timeout;
    loop {
        let exited = child
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .try_wait()
            .ok()
            .flatten()
            .is_some();
        if exited {
            return;
        }
        if Instant::now() >= deadline {
            let _ = child
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .kill();
            return;
        }
        thread::sleep(Duration::from_millis(25));
    }
}

fn method_timeout(method: &str) -> Option<Duration> {
    let seconds = match method {
        "app.bootstrap"
        | "app.restartEngine"
        | "app.shutdown"
        | "settings.get"
        | "settings.update"
        | "modes.select"
        | "audio.listDevices"
        | "providers.list"
        | "models.list"
        | "models.download"
        | "models.cancel"
        | "models.remove"
        | "compute.capabilities"
        | "compute.list" => 5,
        "dictation.start" | "dictation.stop" | "dictation.cancel" => 8,
        "audio.testDevice" | "diagnostics.run" | "compute.probe" | "compute.test" => 30,
        _ => return None,
    };
    Some(Duration::from_secs(seconds))
}

fn stable_error_code(code: &str) -> bool {
    matches!(
        code,
        "INVALID_ARGUMENT"
            | "BUSY"
            | "NOT_FOUND"
            | "UNAVAILABLE"
            | "PERMISSION_DENIED"
            | "PROVIDER_ERROR"
            | "PROTOCOL_MISMATCH"
            | "INTERNAL"
    )
}

fn safe_error_message(code: &str) -> &'static str {
    match code {
        "INVALID_ARGUMENT" => "The engine rejected an invalid request.",
        "BUSY" => "OpenWhisper is already handling another capture operation.",
        "NOT_FOUND" => "The requested engine item was not found.",
        "UNAVAILABLE" => "The OpenWhisper engine is unavailable.",
        "PERMISSION_DENIED" => "OpenWhisper does not have the required permission.",
        "PROVIDER_ERROR" => "The selected provider could not complete the request.",
        "PROTOCOL_MISMATCH" => "The OpenWhisper engine protocol is incompatible.",
        _ => "The OpenWhisper engine could not complete the request.",
    }
}

fn read_bounded_line<R: BufRead>(reader: &mut R, output: &mut Vec<u8>) -> io::Result<usize> {
    let starting_length = output.len();
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return Ok(output.len() - starting_length);
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let take = newline.map_or(available.len(), |position| position + 1);
        if output.len().saturating_add(take) > MAX_FRAME_BYTES + 1 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "engine frame exceeded the private protocol limit",
            ));
        }
        output.extend_from_slice(&available[..take]);
        reader.consume(take);
        if newline.is_some() {
            return Ok(output.len() - starting_length);
        }
    }
}

fn is_active_state(state: &str) -> bool {
    matches!(state, "recording" | "processing" | "cleaning" | "inserting")
}

fn sanitize_log_line(line: &str) -> String {
    let mut sanitized = line.chars().take(800).collect::<String>();
    for marker in ["api_key", "token", "authorization", "transcript"] {
        if sanitized.to_ascii_lowercase().contains(marker) {
            sanitized = "Engine emitted a redacted operational message.".into();
            break;
        }
    }
    sanitized
}

fn forward_engine_stderr(stderr: impl std::io::Read, mut emit: impl FnMut(String)) {
    let reader = BufReader::new(stderr);
    let mut previous = String::new();
    let mut repeated = 0_u64;
    for line in reader.lines().map_while(Result::ok) {
        let safe = sanitize_log_line(&line);
        if safe.is_empty() {
            continue;
        }
        if safe == previous {
            repeated = repeated.saturating_add(1);
            continue;
        }
        if repeated > 0 {
            emit(format!(
                "Previous engine message repeated {repeated} additional times."
            ));
        }
        previous = safe.clone();
        repeated = 0;
        emit(safe);
    }
    if repeated > 0 {
        emit(format!(
            "Previous engine message repeated {repeated} additional times."
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    fn fake_engine() -> EngineCommand {
        EngineCommand {
            program: PathBuf::from("python3"),
            args: vec![PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/fake_engine.py")
                .display()
                .to_string()],
        }
    }

    fn child(supervisor: &EngineSupervisor) -> Arc<Mutex<Child>> {
        Arc::clone(
            &supervisor
                .lock_lifecycle()
                .process
                .as_ref()
                .expect("engine process")
                .child,
        )
    }

    #[test]
    fn allowlist_rejects_unknown_methods() {
        assert!(method_timeout("settings.get").is_some());
        assert!(method_timeout("models.list").is_some());
        assert!(method_timeout("compute.list").is_some());
        assert!(method_timeout("compute.probe").is_some());
        assert!(method_timeout("shell.execute").is_none());
    }

    #[test]
    fn active_states_are_not_silently_restarted() {
        assert!(is_active_state("recording"));
        assert!(is_active_state("processing"));
        assert!(!is_active_state("idle"));
    }

    #[test]
    fn launches_handshakes_and_routes_events() {
        let (sender, receiver) = mpsc::channel();
        let supervisor = EngineSupervisor::new(
            fake_engine(),
            Arc::new(move |event| {
                let _ = sender.send(event);
            }),
        );
        supervisor.start().expect("fake engine starts");
        let bootstrap = supervisor
            .request("app.bootstrap", json!({}))
            .expect("bootstrap succeeds");
        assert_eq!(bootstrap["protocolVersion"], 1);
        supervisor
            .request("dictation.start", json!({}))
            .expect("dictation starts");
        let event = receiver.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(event["event"], "dictation.state");
        assert_eq!(supervisor.dictation_state(), "recording");
        supervisor.shutdown();
    }

    #[test]
    fn sanitizes_sensitive_operational_logs() {
        assert_eq!(
            sanitize_log_line("authorization: secret"),
            "Engine emitted a redacted operational message."
        );
        assert_eq!(sanitize_log_line("portal ready"), "portal ready");
    }

    #[test]
    fn repeated_engine_logs_are_drained_but_reported_once() {
        let input = b"socket warning\nsocket warning\nsocket warning\nportal ready\n";
        let mut emitted = Vec::new();

        forward_engine_stderr(input.as_slice(), |line| emitted.push(line));

        assert_eq!(
            emitted,
            vec![
                "socket warning",
                "Previous engine message repeated 2 additional times.",
                "portal ready",
            ]
        );
    }

    #[test]
    fn bounded_reader_rejects_an_oversized_frame() {
        let input = vec![b'a'; MAX_FRAME_BYTES + 2];
        let mut reader = BufReader::new(input.as_slice());
        let mut output = Vec::new();
        assert_eq!(
            read_bounded_line(&mut reader, &mut output)
                .expect_err("oversized line is rejected")
                .kind(),
            io::ErrorKind::InvalidData
        );
    }

    #[test]
    fn stale_events_are_not_forwarded() {
        let (sender, receiver) = mpsc::channel();
        let supervisor = EngineSupervisor::new(
            fake_engine(),
            Arc::new(move |event| {
                let _ = sender.send(event);
            }),
        );
        supervisor.accept_event(json!({
            "v": 1, "kind": "event", "seq": 2,
            "event": "notice", "payload": {"message": "new"}
        }));
        supervisor.accept_event(json!({
            "v": 1, "kind": "event", "seq": 1,
            "event": "notice", "payload": {"message": "stale"}
        }));
        assert_eq!(receiver.recv().unwrap()["seq"], 2);
        assert!(receiver.try_recv().is_err());
    }

    #[test]
    fn request_timeout_removes_the_pending_id() {
        let supervisor = EngineSupervisor::new(fake_engine(), Arc::new(|_| {}));
        supervisor.start().unwrap();
        let error = supervisor
            .request_running("fixture.hang", json!({}), Duration::from_millis(30))
            .expect_err("fixture intentionally does not respond");
        assert_eq!(error.code, "UNAVAILABLE");
        assert!(supervisor.lock_pending().is_empty());
        supervisor.shutdown();
    }

    #[test]
    fn idle_crash_restarts_once() {
        let supervisor = EngineSupervisor::new(fake_engine(), Arc::new(|_| {}));
        supervisor.start().unwrap();
        let first_generation = supervisor.lock_lifecycle().generation;
        child(&supervisor).lock().unwrap().kill().unwrap();
        thread::sleep(Duration::from_millis(800));
        let lifecycle = supervisor.lock_lifecycle();
        assert_eq!(lifecycle.generation, first_generation + 1);
        assert!(lifecycle.process.is_some());
        assert!(!lifecycle.idle_restart_available);
        drop(lifecycle);
        supervisor.shutdown();
    }

    #[test]
    fn explicit_restart_rehandshakes_only_when_idle() {
        let supervisor = EngineSupervisor::new(fake_engine(), Arc::new(|_| {}));
        supervisor.start().unwrap();
        let first_generation = supervisor.lock_lifecycle().generation;

        let bootstrap = supervisor
            .request("app.restartEngine", json!({}))
            .expect("idle restart succeeds");
        assert_eq!(bootstrap["protocolVersion"], 1);
        assert!(supervisor.lock_lifecycle().generation > first_generation);
        supervisor.shutdown();
    }

    #[test]
    fn explicit_restart_rejects_active_dictation() {
        let (sender, receiver) = mpsc::channel();
        let supervisor = EngineSupervisor::new(
            fake_engine(),
            Arc::new(move |event| {
                let _ = sender.send(event);
            }),
        );
        supervisor.start().unwrap();
        supervisor
            .request("dictation.start", json!({}))
            .expect("dictation starts");
        receiver
            .recv_timeout(Duration::from_secs(1))
            .expect("dictation state event");

        let error = supervisor
            .request("app.restartEngine", json!({}))
            .expect_err("restart is not allowed during dictation");
        assert_eq!(error.code, "BUSY");
        supervisor.shutdown();
    }

    #[test]
    fn recording_crash_latches_fatal_until_explicit_bootstrap() {
        let (sender, receiver) = mpsc::channel();
        let supervisor = EngineSupervisor::new(
            fake_engine(),
            Arc::new(move |event| {
                let _ = sender.send(event);
            }),
        );
        supervisor.start().unwrap();
        supervisor.request("dictation.start", json!({})).unwrap();
        assert_eq!(
            receiver.recv_timeout(Duration::from_secs(1)).unwrap()["event"],
            "dictation.state"
        );
        child(&supervisor).lock().unwrap().kill().unwrap();
        assert_eq!(
            receiver.recv_timeout(Duration::from_secs(1)).unwrap()["event"],
            "engine.fatal"
        );
        assert!(supervisor.request("settings.get", json!({})).is_err());
        assert!(supervisor.request("app.bootstrap", json!({})).is_ok());
        supervisor.shutdown();
    }

    #[test]
    fn shutdown_terminates_the_child() {
        let supervisor = EngineSupervisor::new(fake_engine(), Arc::new(|_| {}));
        supervisor.start().unwrap();
        let child = child(&supervisor);
        supervisor.shutdown();
        assert!(child.lock().unwrap().try_wait().unwrap().is_some());
    }
}
