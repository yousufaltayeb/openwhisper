use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use thiserror::Error;
use uuid::Uuid;
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

pub const WORKER_ABI: &str = "openwhisper-worker-1";
pub const WHISPER_CPP_VERSION: &str = "1.8.3";
pub const MAX_WORKER_MESSAGE_BYTES: usize = 1024 * 1024;
pub const MAX_STREAM_SAMPLES: usize = 16_000 * 15;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum WorkerBackend {
    #[default]
    Auto,
    Vulkan,
    Cpu,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BackendReport {
    pub requested: WorkerBackend,
    pub actual: String,
    pub device_name: Option<String>,
    pub fallback_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct WorkerRequest {
    pub id: Uuid,
    pub generation: u64,
    #[serde(flatten)]
    pub command: WorkerCommand,
}

#[derive(Debug, Error)]
pub enum TranscribeError {
    #[error("model could not be loaded: {0}")]
    Model(String),
    #[error("audio must be a 16 kHz mono 16-bit PCM WAV")]
    InvalidAudio,
    #[error("audio contains no speech samples")]
    EmptyAudio,
    #[error("unsupported language; expected auto, ar, or en")]
    InvalidLanguage,
    #[error("whisper.cpp transcription failed: {0}")]
    Inference(String),
}

pub struct CachedTranscriber {
    model_path: Option<PathBuf>,
    loaded_backend: Option<String>,
    context: Option<WhisperContext>,
    threads: i32,
}

impl CachedTranscriber {
    pub fn new(threads: u16) -> Self {
        Self {
            model_path: None,
            loaded_backend: None,
            context: None,
            threads: if threads == 0 {
                default_threads()
            } else {
                i32::from(threads)
            },
        }
    }

    pub fn loaded_model(&self) -> Option<&Path> {
        self.model_path.as_deref()
    }

    pub fn transcribe(
        &mut self,
        model_path: &Path,
        audio_path: &Path,
        language: &str,
        backend: WorkerBackend,
    ) -> Result<(String, String, BackendReport), TranscribeError> {
        if !matches!(language, "auto" | "ar" | "en") {
            return Err(TranscribeError::InvalidLanguage);
        }
        let audio = decode_pcm_wav(audio_path)?;
        self.transcribe_samples(model_path, &audio, language, backend)
    }

    pub fn transcribe_samples(
        &mut self,
        model_path: &Path,
        audio: &[f32],
        language: &str,
        backend: WorkerBackend,
    ) -> Result<(String, String, BackendReport), TranscribeError> {
        if !matches!(language, "auto" | "ar" | "en") {
            return Err(TranscribeError::InvalidLanguage);
        }
        if audio.is_empty() || audio.iter().all(|sample| *sample == 0.0) {
            return Err(TranscribeError::EmptyAudio);
        }
        let mut report = resolve_backend(backend)?;
        if self.model_path.as_deref() != Some(model_path)
            || self.loaded_backend.as_deref() != Some(report.actual.as_str())
        {
            let mut params = WhisperContextParameters::default();
            params.use_gpu(report.actual == "vulkan");
            let loaded = WhisperContext::new_with_params(
                model_path
                    .to_str()
                    .ok_or_else(|| TranscribeError::Model("model path is not UTF-8".into()))?,
                params,
            );
            let context = match loaded {
                Ok(context) => context,
                Err(error) if backend == WorkerBackend::Auto && report.actual == "vulkan" => {
                    let mut cpu_params = WhisperContextParameters::default();
                    cpu_params.use_gpu(false);
                    report.actual = "cpu".into();
                    report.device_name = None;
                    report.fallback_reason = Some(format!("Vulkan model load failed: {error}"));
                    WhisperContext::new_with_params(
                        model_path.to_str().expect("validated UTF-8 model path"),
                        cpu_params,
                    )
                    .map_err(|cpu_error| TranscribeError::Model(cpu_error.to_string()))?
                }
                Err(error) => return Err(TranscribeError::Model(error.to_string())),
            };
            self.context = Some(context);
            self.model_path = Some(model_path.to_path_buf());
            self.loaded_backend = Some(report.actual.clone());
        }
        let context = self.context.as_ref().expect("model context loaded");
        let mut state = context
            .create_state()
            .map_err(|error| TranscribeError::Inference(error.to_string()))?;
        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_n_threads(self.threads);
        params.set_translate(false);
        params.set_language(whisper_language(language));
        // whisper.cpp's detect_language flag is language-only mode: it exits
        // after detection instead of producing transcript segments. A null
        // language already enables automatic detection during transcription.
        params.set_detect_language(false);
        params.set_print_special(false);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_timestamps(false);
        state
            .full(params, &audio)
            .map_err(|error| TranscribeError::Inference(error.to_string()))?;
        let mut text = String::new();
        for segment in state.as_iter() {
            text.push_str(
                &segment
                    .to_str_lossy()
                    .map_err(|error| TranscribeError::Inference(error.to_string()))?,
            );
        }
        let detected = if language == "auto" {
            whisper_rs::get_lang_str(state.full_lang_id_from_state()).unwrap_or("auto")
        } else {
            language
        };
        Ok((text.trim().to_owned(), detected.to_owned(), report))
    }
}

pub fn resolve_backend(requested: WorkerBackend) -> Result<BackendReport, TranscribeError> {
    #[cfg(target_os = "linux")]
    let vulkan_device = probe_vulkan_device();
    #[cfg(not(target_os = "linux"))]
    let vulkan_device: Option<String> = None;

    match requested {
        WorkerBackend::Cpu => Ok(BackendReport {
            requested,
            actual: "cpu".into(),
            device_name: None,
            fallback_reason: None,
        }),
        WorkerBackend::Vulkan => vulkan_device
            .map(|device_name| BackendReport {
                requested,
                actual: "vulkan".into(),
                device_name: Some(device_name),
                fallback_reason: None,
            })
            .ok_or_else(|| {
                TranscribeError::Model(
                    "Vulkan was explicitly requested, but no Vulkan device is available".into(),
                )
            }),
        WorkerBackend::Auto => Ok(match vulkan_device {
            Some(device_name) => BackendReport {
                requested,
                actual: "vulkan".into(),
                device_name: Some(device_name),
                fallback_reason: None,
            },
            None => BackendReport {
                requested,
                actual: "cpu".into(),
                device_name: None,
                fallback_reason: Some("No Vulkan device was reported by whisper.cpp".into()),
            },
        }),
    }
}

#[cfg(target_os = "linux")]
fn probe_vulkan_device() -> Option<String> {
    let output = std::process::Command::new("vulkaninfo")
        .arg("--summary")
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout
        .lines()
        .find_map(|line| {
            line.trim().strip_prefix("deviceName").and_then(|value| {
                value
                    .split_once('=')
                    .map(|(_, name)| name.trim().to_owned())
            })
        })
        .filter(|name| !name.is_empty())
}

pub fn decode_pcm_wav(audio_path: &Path) -> Result<Vec<f32>, TranscribeError> {
    let mut reader =
        hound::WavReader::open(audio_path).map_err(|_| TranscribeError::InvalidAudio)?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate != 16_000
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err(TranscribeError::InvalidAudio);
    }
    let samples: Vec<i16> = reader
        .samples::<i16>()
        .collect::<Result<_, _>>()
        .map_err(|_| TranscribeError::InvalidAudio)?;
    if samples.is_empty() || samples.iter().all(|sample| *sample == 0) {
        return Err(TranscribeError::EmptyAudio);
    }
    Ok(samples
        .into_iter()
        .map(|sample| f32::from(sample) / 32768.0)
        .collect())
}

fn default_threads() -> i32 {
    std::thread::available_parallelism()
        .map(|count| count.get().min(8) as i32)
        .unwrap_or(4)
}

fn whisper_language(language: &str) -> Option<&str> {
    (language != "auto").then_some(language)
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;

    #[test]
    fn rejects_malformed_and_silent_wav_before_model_loading() {
        let temp = tempfile::tempdir().unwrap();
        let malformed = temp.path().join("bad.wav");
        std::fs::write(&malformed, b"not wave").unwrap();
        assert!(matches!(
            decode_pcm_wav(&malformed),
            Err(TranscribeError::InvalidAudio)
        ));

        let silent = temp.path().join("silent.wav");
        let mut writer = hound::WavWriter::create(
            &silent,
            hound::WavSpec {
                channels: 1,
                sample_rate: 16_000,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            },
        )
        .unwrap();
        for _ in 0..160 {
            writer.write_sample(0_i16).unwrap();
        }
        writer.finalize().unwrap();
        assert!(matches!(
            decode_pcm_wav(&silent),
            Err(TranscribeError::EmptyAudio)
        ));
    }

    #[test]
    fn auto_language_keeps_transcription_enabled() {
        assert_eq!(whisper_language("auto"), None);
        assert_eq!(whisper_language("ar"), Some("ar"));
        assert_eq!(whisper_language("en"), Some("en"));
    }

    #[test]
    fn cpu_backend_is_forced_and_auto_reports_its_actual_choice() {
        let cpu = resolve_backend(WorkerBackend::Cpu).unwrap();
        assert_eq!(cpu.actual, "cpu");
        assert!(cpu.device_name.is_none());
        let automatic = resolve_backend(WorkerBackend::Auto).unwrap();
        assert!(matches!(automatic.actual.as_str(), "cpu" | "vulkan"));
        if automatic.actual == "cpu" {
            assert!(automatic.fallback_reason.is_some());
        } else {
            assert!(automatic.device_name.is_some());
        }
    }

    #[test]
    fn explicit_vulkan_never_silently_becomes_cpu() {
        match resolve_backend(WorkerBackend::Vulkan) {
            Ok(report) => {
                assert_eq!(report.actual, "vulkan");
                assert!(report.device_name.is_some());
                assert!(report.fallback_reason.is_none());
            }
            Err(error) => assert!(error.to_string().contains("explicitly requested")),
        }
    }

    #[test]
    fn full_fifteen_second_pcm_snapshot_fits_the_bounded_private_channel() {
        let request = WorkerRequest {
            id: Uuid::nil(),
            generation: 1,
            command: WorkerCommand::StreamAppend {
                pcm_base64: base64::engine::general_purpose::STANDARD.encode(vec![
                    0_u8;
                    MAX_STREAM_SAMPLES
                        * 2
                ]),
            },
        };
        assert!(serde_json::to_vec(&request).unwrap().len() < MAX_WORKER_MESSAGE_BYTES);
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "command", rename_all = "snake_case")]
pub enum WorkerCommand {
    Probe,
    Transcribe {
        model_path: String,
        audio_path: String,
        language: String,
        #[serde(default)]
        backend: WorkerBackend,
    },
    StreamStart {
        model_path: String,
        language: String,
        #[serde(default)]
        backend: WorkerBackend,
    },
    StreamAppend {
        pcm_base64: String,
    },
    StreamFinish,
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
        requested_backend: WorkerBackend,
        device_name: Option<String>,
        fallback_reason: Option<String>,
    },
    Transcript {
        id: Uuid,
        generation: u64,
        text: String,
        language: String,
        backend: BackendReport,
    },
    StreamStarted {
        id: Uuid,
        generation: u64,
        backend: BackendReport,
    },
    StreamHypothesis {
        id: Uuid,
        generation: u64,
        text: String,
        language: String,
        latency_ms: u64,
        backend: BackendReport,
    },
    StreamFinished {
        id: Uuid,
        generation: u64,
        text: String,
        language: String,
        latency_ms: u64,
        backend: BackendReport,
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
    pub fn id(&self) -> Option<Uuid> {
        match self {
            Self::Ready { .. } => None,
            Self::Probe { id, .. }
            | Self::Transcript { id, .. }
            | Self::StreamStarted { id, .. }
            | Self::StreamHypothesis { id, .. }
            | Self::StreamFinished { id, .. }
            | Self::Cancelled { id, .. } => Some(*id),
            Self::Error { id, .. } => *id,
        }
    }

    pub fn generation(&self) -> Option<u64> {
        match self {
            Self::Ready { .. } => None,
            Self::Probe { generation, .. }
            | Self::Transcript { generation, .. }
            | Self::StreamStarted { generation, .. }
            | Self::StreamHypothesis { generation, .. }
            | Self::StreamFinished { generation, .. }
            | Self::Cancelled { generation, .. }
            | Self::Error { generation, .. } => Some(*generation),
        }
    }
}
