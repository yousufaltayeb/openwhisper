use thiserror::Error;

pub const INTERNAL_SAMPLE_RATE: u32 = 16_000;

#[derive(Debug, Clone, PartialEq)]
pub struct AudioBuffer {
    pub sample_rate: u32,
    pub channels: u16,
    pub interleaved: Vec<f32>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum AudioError {
    #[error("sample rate and channel count must be non-zero")]
    InvalidFormat,
    #[error("interleaved sample count is not divisible by the channel count")]
    IncompleteFrame,
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
}
