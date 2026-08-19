use std::io::{self, BufRead, Write};

use openwhisper_worker_native::{
    MAX_WORKER_MESSAGE_BYTES, WORKER_ABI, WorkerCommand, WorkerRequest, WorkerResponse,
};

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
            WorkerCommand::Probe => write_response(
                &mut output,
                &WorkerResponse::Probe {
                    id: request.id,
                    generation: request.generation,
                    cpu: true,
                    backend: "cpu".into(),
                },
            )?,
            WorkerCommand::Cancel => write_response(
                &mut output,
                &WorkerResponse::Cancelled {
                    id: request.id,
                    generation: request.generation,
                },
            )?,
            WorkerCommand::Shutdown => break,
            WorkerCommand::Transcribe {
                model_path,
                audio_path,
                ..
            } => {
                let (code, message) = if !std::path::Path::new(&model_path).exists() {
                    ("model_unavailable", "verified model file is unavailable")
                } else if !std::path::Path::new(&audio_path).exists() {
                    ("audio_unavailable", "normalized audio file is unavailable")
                } else {
                    (
                        "backend_not_linked",
                        "whisper.cpp runtime pack is not linked in this alpha build",
                    )
                };
                write_response(
                    &mut output,
                    &WorkerResponse::Error {
                        id: Some(request.id),
                        generation: request.generation,
                        code: code.into(),
                        message: message.into(),
                    },
                )?;
            }
        }
    }
    Ok(())
}
