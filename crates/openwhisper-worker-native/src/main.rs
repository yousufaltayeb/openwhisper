use base64::Engine;
use std::io::{self, BufRead, Write};
use std::time::Instant;

use openwhisper_worker_native::{
    BackendReport, CachedTranscriber, MAX_STREAM_SAMPLES, MAX_WORKER_MESSAGE_BYTES, WORKER_ABI,
    WorkerBackend, WorkerCommand, WorkerRequest, WorkerResponse, resolve_backend,
};

struct StreamSession {
    model_path: String,
    language: String,
    backend: WorkerBackend,
    samples: Vec<i16>,
    last_text: String,
    last_language: String,
    report: BackendReport,
}

fn write_response(output: &mut impl Write, response: &WorkerResponse) -> io::Result<()> {
    serde_json::to_writer(&mut *output, response)?;
    output.write_all(b"\n")?;
    output.flush()
}

fn read_bounded_line(reader: &mut impl BufRead) -> io::Result<Option<Result<String, ()>>> {
    let mut bytes = Vec::new();
    let mut oversized = false;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            if bytes.is_empty() && !oversized {
                return Ok(None);
            }
            break;
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let take = newline.unwrap_or(available.len());
        if !oversized {
            if bytes.len() + take > MAX_WORKER_MESSAGE_BYTES {
                oversized = true;
            } else {
                bytes.extend_from_slice(&available[..take]);
            }
        }
        reader.consume(take + usize::from(newline.is_some()));
        if newline.is_some() {
            break;
        }
    }
    if oversized {
        return Ok(Some(Err(())));
    }
    let line = String::from_utf8(bytes)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    Ok(Some(Ok(line)))
}

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut output = io::stdout().lock();
    write_response(
        &mut output,
        &WorkerResponse::Ready {
            abi: WORKER_ABI.into(),
        },
    )?;
    let mut input = stdin.lock();
    let threads = std::env::var("OPENWHISPER_WORKER_THREADS")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(0);
    let mut transcriber = CachedTranscriber::new(threads);
    let mut stream: Option<StreamSession> = None;
    while let Some(line) = read_bounded_line(&mut input)? {
        let line = match line {
            Ok(line) => line,
            Err(()) => {
                write_response(
                    &mut output,
                    &WorkerResponse::Error {
                        id: None,
                        generation: 0,
                        code: "oversized_message".into(),
                        message: "worker request exceeded 1 MiB".into(),
                    },
                )?;
                continue;
            }
        };
        let request: WorkerRequest = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(_) => {
                write_response(
                    &mut output,
                    &WorkerResponse::Error {
                        id: None,
                        generation: 0,
                        code: "invalid_json".into(),
                        message: "worker request was malformed".into(),
                    },
                )?;
                continue;
            }
        };
        match request.command {
            WorkerCommand::Probe => {
                let report = resolve_backend(WorkerBackend::Auto).unwrap_or(BackendReport {
                    requested: WorkerBackend::Auto,
                    actual: "cpu".into(),
                    device_name: None,
                    fallback_reason: Some("Vulkan probe failed".into()),
                });
                write_response(
                    &mut output,
                    &WorkerResponse::Probe {
                        id: request.id,
                        generation: request.generation,
                        cpu: true,
                        backend: report.actual,
                        requested_backend: report.requested,
                        device_name: report.device_name,
                        fallback_reason: report.fallback_reason,
                    },
                )?
            }
            WorkerCommand::Cancel => {
                stream = None;
                write_response(
                    &mut output,
                    &WorkerResponse::Cancelled {
                        id: request.id,
                        generation: request.generation,
                    },
                )?
            }
            WorkerCommand::Shutdown => break,
            WorkerCommand::Transcribe {
                model_path,
                audio_path,
                language,
                backend,
            } => {
                let error = if !std::path::Path::new(&model_path).exists() {
                    Some((
                        "model_unavailable".into(),
                        "verified model file is unavailable".into(),
                    ))
                } else if !std::path::Path::new(&audio_path).exists() {
                    Some((
                        "audio_unavailable".into(),
                        "normalized audio file is unavailable".into(),
                    ))
                } else {
                    match transcriber.transcribe(
                        std::path::Path::new(&model_path),
                        std::path::Path::new(&audio_path),
                        &language,
                        backend,
                    ) {
                        Ok((text, language, report)) => {
                            write_response(
                                &mut output,
                                &WorkerResponse::Transcript {
                                    id: request.id,
                                    generation: request.generation,
                                    text,
                                    language,
                                    backend: report,
                                },
                            )?;
                            None
                        }
                        Err(error) => Some(("transcription_failed".into(), error.to_string())),
                    }
                };
                if let Some((code, message)) = error {
                    write_response(
                        &mut output,
                        &WorkerResponse::Error {
                            id: Some(request.id),
                            generation: request.generation,
                            code,
                            message,
                        },
                    )?;
                }
            }
            WorkerCommand::StreamStart {
                model_path,
                language,
                backend,
            } => {
                let response = if !std::path::Path::new(&model_path).exists() {
                    WorkerResponse::Error {
                        id: Some(request.id),
                        generation: request.generation,
                        code: "model_unavailable".into(),
                        message: "verified model file is unavailable".into(),
                    }
                } else {
                    match resolve_backend(backend) {
                        Ok(report) => {
                            stream = Some(StreamSession {
                                model_path,
                                language,
                                backend,
                                samples: Vec::new(),
                                last_text: String::new(),
                                last_language: "auto".into(),
                                report: report.clone(),
                            });
                            WorkerResponse::StreamStarted {
                                id: request.id,
                                generation: request.generation,
                                backend: report,
                            }
                        }
                        Err(error) => WorkerResponse::Error {
                            id: Some(request.id),
                            generation: request.generation,
                            code: "backend_unavailable".into(),
                            message: error.to_string(),
                        },
                    }
                };
                write_response(&mut output, &response)?;
            }
            WorkerCommand::StreamAppend { pcm_base64 } => {
                let Some(active) = stream.as_mut() else {
                    write_response(
                        &mut output,
                        &WorkerResponse::Error {
                            id: Some(request.id),
                            generation: request.generation,
                            code: "stream_inactive".into(),
                            message: "stream_start is required before stream_append".into(),
                        },
                    )?;
                    continue;
                };
                let pcm = match base64::engine::general_purpose::STANDARD.decode(pcm_base64) {
                    Ok(pcm) if pcm.len() % 2 == 0 => pcm,
                    _ => {
                        write_response(
                            &mut output,
                            &WorkerResponse::Error {
                                id: Some(request.id),
                                generation: request.generation,
                                code: "invalid_audio".into(),
                                message: "stream PCM must be base64-encoded little-endian i16"
                                    .into(),
                            },
                        )?;
                        continue;
                    }
                };
                active.samples.extend(
                    pcm.chunks_exact(2)
                        .map(|pair| i16::from_le_bytes([pair[0], pair[1]])),
                );
                if active.samples.len() > MAX_STREAM_SAMPLES {
                    active
                        .samples
                        .drain(..active.samples.len() - MAX_STREAM_SAMPLES);
                }
                let audio: Vec<f32> = active
                    .samples
                    .iter()
                    .map(|sample| f32::from(*sample) / 32768.0)
                    .collect();
                let started = Instant::now();
                match transcriber.transcribe_samples(
                    std::path::Path::new(&active.model_path),
                    &audio,
                    &active.language,
                    active.backend,
                ) {
                    Ok((text, language, report)) => {
                        active.last_text = text.clone();
                        active.last_language = language.clone();
                        active.report = report.clone();
                        write_response(
                            &mut output,
                            &WorkerResponse::StreamHypothesis {
                                id: request.id,
                                generation: request.generation,
                                text,
                                language,
                                latency_ms: started.elapsed().as_millis() as u64,
                                backend: report,
                            },
                        )?;
                    }
                    Err(openwhisper_worker_native::TranscribeError::EmptyAudio) => write_response(
                        &mut output,
                        &WorkerResponse::StreamHypothesis {
                            id: request.id,
                            generation: request.generation,
                            text: String::new(),
                            language: active.last_language.clone(),
                            latency_ms: started.elapsed().as_millis() as u64,
                            backend: active.report.clone(),
                        },
                    )?,
                    Err(error) => write_response(
                        &mut output,
                        &WorkerResponse::Error {
                            id: Some(request.id),
                            generation: request.generation,
                            code: "transcription_failed".into(),
                            message: error.to_string(),
                        },
                    )?,
                }
            }
            WorkerCommand::StreamFinish => {
                let response = match stream.take() {
                    Some(active) => WorkerResponse::StreamFinished {
                        id: request.id,
                        generation: request.generation,
                        text: active.last_text,
                        language: active.last_language,
                        latency_ms: 0,
                        backend: active.report,
                    },
                    None => WorkerResponse::Error {
                        id: Some(request.id),
                        generation: request.generation,
                        code: "stream_inactive".into(),
                        message: "stream_start is required before stream_finish".into(),
                    },
                };
                write_response(&mut output, &response)?;
            }
        }
    }
    Ok(())
}
