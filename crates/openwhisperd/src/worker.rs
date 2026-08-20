use std::path::{Path, PathBuf};
use std::time::Duration;

use openwhisper_worker_native::{
    MAX_WORKER_MESSAGE_BYTES, WORKER_ABI, WorkerRequest, WorkerResponse,
};
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, BufWriter, Lines};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::time::{Instant, timeout_at};

#[derive(Debug, Error)]
pub enum SupervisorError {
    #[error("worker I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("worker protocol failed: {0}")]
    Json(#[from] serde_json::Error),
    #[error("worker request exceeded its bounded channel")]
    Oversized,
    #[error("worker did not respond before its deadline")]
    Timeout,
    #[error("worker crashed or closed stdout")]
    Crashed,
    #[error("worker ABI is incompatible: {0}")]
    IncompatibleAbi(String),
}

pub struct WorkerSupervisor {
    executable: PathBuf,
    threads: u16,
    child: Child,
    input: BufWriter<ChildStdin>,
    output: Lines<BufReader<ChildStdout>>,
}

impl WorkerSupervisor {
    pub async fn spawn(executable: impl AsRef<Path>) -> Result<Self, SupervisorError> {
        Self::spawn_with_threads(executable, 0).await
    }

    pub async fn spawn_with_threads(
        executable: impl AsRef<Path>,
        threads: u16,
    ) -> Result<Self, SupervisorError> {
        let executable = executable.as_ref().to_path_buf();
        let mut command = Command::new(&executable);
        if threads > 0 {
            command.env("OPENWHISPER_WORKER_THREADS", threads.to_string());
        }
        let mut child = command
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true)
            .spawn()?;
        let input = BufWriter::new(child.stdin.take().ok_or(SupervisorError::Crashed)?);
        let output = BufReader::new(child.stdout.take().ok_or(SupervisorError::Crashed)?).lines();
        let mut supervisor = Self {
            executable,
            threads,
            child,
            input,
            output,
        };
        match supervisor.read_response(0, Duration::from_secs(2)).await? {
            WorkerResponse::Ready { abi } if abi == WORKER_ABI => Ok(supervisor),
            WorkerResponse::Ready { abi } => Err(SupervisorError::IncompatibleAbi(abi)),
            _ => Err(SupervisorError::IncompatibleAbi(
                "missing ready frame".into(),
            )),
        }
    }

    pub async fn request(
        &mut self,
        request: &WorkerRequest,
        timeout: Duration,
    ) -> Result<WorkerResponse, SupervisorError> {
        let payload = serde_json::to_vec(request)?;
        if payload.len() > MAX_WORKER_MESSAGE_BYTES {
            return Err(SupervisorError::Oversized);
        }
        self.input.write_all(&payload).await?;
        self.input.write_all(b"\n").await?;
        self.input.flush().await?;
        self.read_response(request.generation, timeout).await
    }

    pub async fn restart(&mut self) -> Result<(), SupervisorError> {
        let _ = self.child.kill().await;
        let replacement = Self::spawn_with_threads(&self.executable, self.threads).await?;
        *self = replacement;
        Ok(())
    }

    pub async fn shutdown(mut self) -> Result<(), SupervisorError> {
        self.child.kill().await?;
        let _ = self.child.wait().await;
        Ok(())
    }

    async fn read_response(
        &mut self,
        generation: u64,
        timeout: Duration,
    ) -> Result<WorkerResponse, SupervisorError> {
        let deadline = Instant::now() + timeout;
        loop {
            let line = timeout_at(deadline, self.output.next_line())
                .await
                .map_err(|_| SupervisorError::Timeout)??
                .ok_or(SupervisorError::Crashed)?;
            if line.len() > MAX_WORKER_MESSAGE_BYTES {
                return Err(SupervisorError::Oversized);
            }
            let response: WorkerResponse = serde_json::from_str(&line)?;
            if generation == 0 || response.generation() == Some(generation) {
                return Ok(response);
            }
        }
    }
}
