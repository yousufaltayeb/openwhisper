use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use thiserror::Error;
use uuid::Uuid;
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

pub const WORKER_ABI: &str = "openwhisper-worker-1";
pub const WHISPER_CPP_VERSION: &str = "1.8.3";
pub const MAX_WORKER_MESSAGE_BYTES: usize = 1024 * 1024;

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
    context: Option<WhisperContext>,
    threads: i32,
}

impl CachedTranscriber {
    pub fn new(threads: u16) -> Self {
        Self {
            model_path: None,
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
    ) -> Result<(String, String), TranscribeError> {
        if !matches!(language, "auto" | "ar" | "en") {
            return Err(TranscribeError::InvalidLanguage);
        }
        let audio = decode_pcm_wav(audio_path)?;
        if self.model_path.as_deref() != Some(model_path) {
            let context = WhisperContext::new_with_params(
                model_path
                    .to_str()
                    .ok_or_else(|| TranscribeError::Model("model path is not UTF-8".into()))?,
                WhisperContextParameters::default(),
            )
            .map_err(|error| TranscribeError::Model(error.to_string()))?;
            self.context = Some(context);
            self.model_path = Some(model_path.to_path_buf());
        }
        let context = self.context.as_ref().expect("model context loaded");
        let mut state = context
            .create_state()
            .map_err(|error| TranscribeError::Inference(error.to_string()))?;
        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_n_threads(self.threads);
        params.set_translate(false);
        params.set_language((language != "auto").then_some(language));
        params.set_detect_language(language == "auto");
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
        Ok((text.trim().to_owned(), detected.to_owned()))
    }
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

#[cfg(test)]
mod tests {
    use super::*;

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
