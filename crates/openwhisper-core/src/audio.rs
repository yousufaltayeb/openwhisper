use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use thiserror::Error;
use tokio::fs::{self, File, OpenOptions};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::{Child, Command};
use tokio::task::JoinHandle;
use tokio::time::timeout;
use uuid::Uuid;

use crate::config::{AudioBackend, AudioConfig};

pub const INTERNAL_SAMPLE_RATE: u32 = 16_000;

#[derive(Debug, Clone, PartialEq)]
pub struct AudioBuffer {
    pub sample_rate: u32,
    pub channels: u16,
    pub interleaved: Vec<f32>,
}

#[derive(Debug, Error)]
pub enum AudioError {
    #[error("sample rate and channel count must be non-zero")]
    InvalidFormat,
    #[error("interleaved sample count is not divisible by the channel count")]
    IncompleteFrame,
    #[error("no requested Linux capture backend is installed")]
    BackendUnavailable,
    #[error("capture backend could not start: {0}")]
    Startup(String),
    #[error("capture backend exited before recording was stopped")]
    EarlyExit,
    #[error("microphone permission was denied")]
    PermissionDenied,
    #[error("captured audio exceeded the configured bounded writer")]
    Overflow,
    #[error("capture produced no audio")]
    Empty,
    #[error("capture backend did not stop within two seconds")]
    StopTimeout,
    #[error("audio I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("capture writer task failed")]
    WriterTask,
}

impl PartialEq for AudioError {
    fn eq(&self, other: &Self) -> bool {
        std::mem::discriminant(self) == std::mem::discriminant(other)
    }
}
impl Eq for AudioError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureBackend {
    Pipewire,
    Pulse,
    Alsa,
}

impl CaptureBackend {
    pub fn executable(self) -> &'static str {
        match self {
            Self::Pipewire => "pw-record",
            Self::Pulse => "parec",
            Self::Alsa => "arecord",
        }
    }

    pub fn args(self, device: &str) -> Vec<String> {
        let mut args: Vec<String> = match self {
            Self::Pipewire => [
                "--raw",
                "--format",
                "s16",
                "--rate",
                "16000",
                "--channels",
                "1",
                "-",
            ]
            .into_iter()
            .map(str::to_owned)
            .collect(),
            Self::Pulse => ["--raw", "--format=s16le", "--rate=16000", "--channels=1"]
                .into_iter()
                .map(str::to_owned)
                .collect(),
            Self::Alsa => ["-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]
                .into_iter()
                .map(str::to_owned)
                .collect(),
        };
        if !device.is_empty() {
            match self {
                Self::Pipewire => {
                    args.insert(0, device.into());
                    args.insert(0, "--target".into());
                }
                Self::Pulse => args.insert(0, format!("--device={device}")),
                Self::Alsa => {
                    args.insert(0, device.into());
                    args.insert(0, "-D".into());
                }
            }
        }
        args
    }
}

pub struct ActiveCapture {
    pub session_id: Uuid,
    pub backend: CaptureBackend,
    pub partial_path: PathBuf,
    pub wav_path: PathBuf,
    child: Child,
    writer: JoinHandle<Result<u64, AudioError>>,
}

impl ActiveCapture {
    pub async fn start(root: &Path, config: &AudioConfig) -> Result<Self, AudioError> {
        let candidates: Vec<CaptureBackend> = match config.backend {
            AudioBackend::Auto => vec![
                CaptureBackend::Pipewire,
                CaptureBackend::Pulse,
                CaptureBackend::Alsa,
            ],
            AudioBackend::Pipewire => vec![CaptureBackend::Pipewire],
            AudioBackend::Pulse => vec![CaptureBackend::Pulse],
            AudioBackend::Alsa => vec![CaptureBackend::Alsa],
        };
        let mut last_error = None;
        for backend in candidates {
            match Self::start_backend(root, config, backend).await {
                Ok(capture) => return Ok(capture),
                Err(AudioError::Startup(message)) => last_error = Some(message),
                Err(AudioError::EarlyExit) => {
                    last_error = Some(format!("{} exited during preflight", backend.executable()))
                }
                Err(AudioError::BackendUnavailable) => {}
                Err(error) => return Err(error),
            }
        }
        Err(last_error
            .map(AudioError::Startup)
            .unwrap_or(AudioError::BackendUnavailable))
    }

    async fn start_backend(
        root: &Path,
        config: &AudioConfig,
        backend: CaptureBackend,
    ) -> Result<Self, AudioError> {
        let Some(executable) = find_executable(backend.executable()) else {
            return Err(AudioError::BackendUnavailable);
        };
        let session_id = Uuid::new_v4();
        let session_dir = root.join(session_id.to_string());
        fs::create_dir_all(&session_dir).await?;
        set_private_dir(&session_dir).await?;
        let partial_path = session_dir.join("capture.pcm.partial");
        let wav_path = session_dir.join("capture.wav");
        let file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&partial_path)
            .await?;
        set_private_file(&partial_path).await?;
        let mut child = Command::new(executable)
            .args(backend.args(&config.device))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| AudioError::Startup(error.to_string()))?;
        let mut stdout = child
            .stdout
            .take()
            .ok_or_else(|| AudioError::Startup("capture stdout was unavailable".into()))?;
        tokio::time::sleep(Duration::from_millis(75)).await;
        if child.try_wait()?.is_some() {
            let _ = fs::remove_dir_all(&session_dir).await;
            return Err(AudioError::EarlyExit);
        }
        let maximum = u64::from(config.max_recording_seconds) * u64::from(INTERNAL_SAMPLE_RATE) * 2;
        let writer = tokio::spawn(async move {
            let mut file = file;
            let mut total = 0_u64;
            let mut buffer = [0_u8; 16 * 1024];
            loop {
                let read = stdout.read(&mut buffer).await?;
                if read == 0 {
                    break;
                }
                total += read as u64;
                if total > maximum {
                    return Err(AudioError::Overflow);
                }
                file.write_all(&buffer[..read]).await?;
            }
            file.flush().await?;
            file.sync_all().await?;
            Ok(total)
        });
        Ok(Self {
            session_id,
            backend,
            partial_path,
            wav_path,
            child,
            writer,
        })
    }

    pub async fn stop(mut self) -> Result<PathBuf, AudioError> {
        if self.child.try_wait()?.is_none() {
            #[cfg(unix)]
            if let Some(pid) = self.child.id() {
                let _ = nix::sys::signal::kill(
                    nix::unistd::Pid::from_raw(pid as i32),
                    nix::sys::signal::Signal::SIGINT,
                );
            }
            #[cfg(not(unix))]
            let _ = self.child.start_kill();
        }
        if timeout(Duration::from_secs(2), self.child.wait())
            .await
            .is_err()
        {
            let _ = self.child.kill().await;
            let _ = self.child.wait().await;
            self.writer.abort();
            self.cleanup().await;
            return Err(AudioError::StopTimeout);
        }
        let session_dir = self.partial_path.parent().map(Path::to_path_buf);
        let bytes = self.writer.await.map_err(|_| AudioError::WriterTask)??;
        if bytes == 0 {
            if let Some(directory) = session_dir {
                let _ = fs::remove_dir_all(directory).await;
            }
            return Err(AudioError::Empty);
        }
        finalize_pcm_wav(&self.partial_path, &self.wav_path, bytes).await?;
        let _ = fs::remove_file(&self.partial_path).await;
        Ok(self.wav_path)
    }

    pub async fn cancel(mut self) {
        let _ = self.child.kill().await;
        let _ = self.child.wait().await;
        self.writer.abort();
        self.cleanup().await;
    }

    async fn cleanup(&self) {
        if let Some(directory) = self.partial_path.parent() {
            let _ = fs::remove_dir_all(directory).await;
        }
    }
}

pub async fn cleanup_stale_sessions(root: &Path) -> Result<(), AudioError> {
    if !root.exists() {
        return Ok(());
    }
    let mut entries = fs::read_dir(root).await?;
    while let Some(entry) = entries.next_entry().await? {
        let path = entry.path();
        if path.is_dir() {
            fs::remove_dir_all(path).await?;
        } else {
            fs::remove_file(path).await?;
        }
    }
    Ok(())
}

fn find_executable(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths)
            .map(|directory| directory.join(name))
            .find(|path| path.is_file())
    })
}

async fn finalize_pcm_wav(raw: &Path, wav: &Path, bytes: u64) -> Result<(), AudioError> {
    let data_len = u32::try_from(bytes).map_err(|_| AudioError::Overflow)?;
    let mut output = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(wav)
        .await?;
    set_private_file(wav).await?;
    let mut header = Vec::with_capacity(44);
    header.extend_from_slice(b"RIFF");
    header.extend_from_slice(&(36_u32 + data_len).to_le_bytes());
    header.extend_from_slice(b"WAVEfmt ");
    header.extend_from_slice(&16_u32.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&INTERNAL_SAMPLE_RATE.to_le_bytes());
    header.extend_from_slice(&(INTERNAL_SAMPLE_RATE * 2).to_le_bytes());
    header.extend_from_slice(&2_u16.to_le_bytes());
    header.extend_from_slice(&16_u16.to_le_bytes());
    header.extend_from_slice(b"data");
    header.extend_from_slice(&data_len.to_le_bytes());
    output.write_all(&header).await?;
    let mut input = File::open(raw).await?;
    tokio::io::copy(&mut input, &mut output).await?;
    output.flush().await?;
    output.sync_all().await?;
    Ok(())
}

#[cfg(unix)]
async fn set_private_dir(path: &Path) -> Result<(), AudioError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).await?;
    Ok(())
}
#[cfg(not(unix))]
async fn set_private_dir(_: &Path) -> Result<(), AudioError> {
    Ok(())
}

#[cfg(unix)]
async fn set_private_file(path: &Path) -> Result<(), AudioError> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).await?;
    Ok(())
}
#[cfg(not(unix))]
async fn set_private_file(_: &Path) -> Result<(), AudioError> {
    Ok(())
}

impl AudioBuffer {
    pub fn normalize_16khz_mono_pcm16(&self) -> Result<Vec<i16>, AudioError> {
        if self.sample_rate == 0 || self.channels == 0 {
            return Err(AudioError::InvalidFormat);
        }
        let channels = usize::from(self.channels);
        if self.interleaved.len() % channels != 0 {
            return Err(AudioError::IncompleteFrame);
        }
        let mono: Vec<f32> = self
            .interleaved
            .chunks_exact(channels)
            .map(|frame| frame.iter().copied().sum::<f32>() / channels as f32)
            .collect();
        if mono.is_empty() {
            return Ok(Vec::new());
        }
        let output_frames = ((mono.len() as u64 * u64::from(INTERNAL_SAMPLE_RATE))
            / u64::from(self.sample_rate)) as usize;
        let ratio = self.sample_rate as f64 / INTERNAL_SAMPLE_RATE as f64;
        Ok((0..output_frames)
            .map(|index| {
                let source = index as f64 * ratio;
                let lower = source.floor() as usize;
                let upper = (lower + 1).min(mono.len() - 1);
                let fraction = (source - lower as f64) as f32;
                let sample = mono[lower] * (1.0 - fraction) + mono[upper] * fraction;
                (sample.clamp(-1.0, 1.0) * i16::MAX as f32).round() as i16
            })
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn downmixes_and_resamples_to_internal_contract() {
        let audio = AudioBuffer {
            sample_rate: 48_000,
            channels: 2,
            interleaved: (0..48_000)
                .flat_map(|index| {
                    let sample = ((index as f32 / 48_000.0) * std::f32::consts::TAU * 440.0).sin();
                    [sample, sample * 0.5]
                })
                .collect(),
        };
        let normalized = audio.normalize_16khz_mono_pcm16().unwrap();
        assert_eq!(normalized.len(), 16_000);
        assert!(normalized.iter().any(|sample| *sample != 0));
    }

    #[test]
    fn clips_host_samples_before_pcm_conversion() {
        let audio = AudioBuffer {
            sample_rate: 16_000,
            channels: 1,
            interleaved: vec![2.0, -2.0],
        };
        assert_eq!(
            audio.normalize_16khz_mono_pcm16().unwrap(),
            vec![i16::MAX, -i16::MAX]
        );
    }

    #[test]
    fn capture_backends_use_exact_shell_free_arguments() {
        assert_eq!(
            CaptureBackend::Pipewire.args(""),
            [
                "--raw",
                "--format",
                "s16",
                "--rate",
                "16000",
                "--channels",
                "1",
                "-"
            ]
        );
        assert_eq!(
            CaptureBackend::Pulse.args("mic"),
            [
                "--device=mic",
                "--raw",
                "--format=s16le",
                "--rate=16000",
                "--channels=1"
            ]
        );
        assert_eq!(
            CaptureBackend::Alsa.args("hw:1"),
            [
                "-D", "hw:1", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"
            ]
        );
    }
}
