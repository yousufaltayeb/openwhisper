#[cfg(unix)]
mod unix {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::Arc;

    use openwhisper_core::{AppPaths, detect_capabilities};
    use openwhisper_protocol::{
        ClientMessage, ErrorCode, RpcError, ServerMessage, protocol_supported, read_frame,
        write_frame,
    };
    use openwhisperd::{DaemonState, InstanceGuard};
    use tokio::net::{UnixListener, UnixStream};
    use tracing::{info, warn};

    pub async fn run() -> anyhow::Result<()> {
        let paths = AppPaths::discover()?;
        paths.ensure()?;
        fs::set_permissions(&paths.runtime_dir, fs::Permissions::from_mode(0o700))?;
        let _instance = InstanceGuard::acquire(&paths)?;
        let socket = paths.socket_file();
        if socket.exists() {
            fs::remove_file(&socket)?;
        }
        let listener = UnixListener::bind(&socket)?;
        fs::set_permissions(&socket, fs::Permissions::from_mode(0o600))?;
        let state = Arc::new(DaemonState::initialize(paths)?);
        state
            .cleanup_runtime_audio()
            .await
            .map_err(|error| anyhow::anyhow!(error.message))?;
        info!(socket = %socket.display(), "openwhisperd ready");

        loop {
            tokio::select! {
                accepted = listener.accept() => {
                    let (stream, _) = accepted?;
                    let state = state.clone();
                    tokio::spawn(async move {
                        if let Err(error) = serve_client(stream, state).await {
                            warn!(error = %error, "client disconnected");
                        }
                    });
                }
                _ = state.shutdown.notified() => break,
                _ = tokio::signal::ctrl_c() => break,
            }
        }
        drop(listener);
        let _ = fs::remove_file(&socket);
        info!("openwhisperd stopped");
        Ok(())
    }

    async fn serve_client(mut stream: UnixStream, state: Arc<DaemonState>) -> anyhow::Result<()> {
        verify_peer(&stream)?;
        let Some(ClientMessage::Handshake {
            protocol_version, ..
        }) = tokio::time::timeout(std::time::Duration::from_secs(5), read_frame(&mut stream))
            .await
            .map_err(|_| anyhow::anyhow!("IPC handshake timed out"))??
        else {
            write_frame(
                &mut stream,
                &ServerMessage::Error {
                    id: None,
                    error: RpcError::new(
                        ErrorCode::Protocol,
                        "the first frame must be a handshake",
                    ),
                },
            )
            .await?;
            return Ok(());
        };
        if !protocol_supported(protocol_version) {
            write_frame(
                &mut stream,
                &ServerMessage::Error {
                    id: None,
                    error: RpcError::new(
                        ErrorCode::Protocol,
                        format!("protocol {protocol_version} is unsupported"),
                    )
                    .with_action(format!(
                        "Use protocol {} or {}",
                        openwhisper_protocol::CURRENT_PROTOCOL_VERSION,
                        openwhisper_protocol::PREVIOUS_PROTOCOL_VERSION
                    )),
                },
            )
            .await?;
            return Ok(());
        }
        write_frame(
            &mut stream,
            &ServerMessage::HandshakeAck {
                protocol_version,
                server_version: env!("CARGO_PKG_VERSION").into(),
                capabilities: Box::new(detect_capabilities()),
            },
        )
        .await?;

        let (mut reader, mut writer) = stream.into_split();
        let mut events = state.subscribe();
        let (responses, mut response_queue) = tokio::sync::mpsc::unbounded_channel();
        let mut subscribed = false;
        let mut subscription_floor = 0_u64;
        loop {
            tokio::select! {
                incoming = read_frame::<_, ClientMessage>(&mut reader) => {
                    let Some(message) = incoming? else { return Ok(()); };
                    match message {
                        ClientMessage::Request { id, method, params } => {
                            let state = state.clone();
                            let responses = responses.clone();
                            tokio::spawn(async move {
                                let response = match state.dispatch(&method, params).await {
                                    Ok(result) => ServerMessage::Response { id, result },
                                    Err(error) => ServerMessage::Error { id: Some(id), error },
                                };
                                let _ = responses.send(response);
                            });
                        }
                        ClientMessage::Subscribe { after_sequence: _ } => {
                            let snapshot = state.snapshot();
                            if let ServerMessage::Snapshot { sequence, .. } = &snapshot { subscription_floor = *sequence; }
                            write_frame(&mut writer, &snapshot).await?;
                            subscribed = true;
                        },
                        ClientMessage::Handshake { .. } => {
                            write_frame(&mut writer, &ServerMessage::Error { id: None, error: RpcError::new(ErrorCode::Protocol, "handshake already completed") }).await?;
                        }
                    }
                }
                response = response_queue.recv() => {
                    let Some(response) = response else { return Ok(()); };
                    write_frame(&mut writer, &response).await?;
                }
                event = events.recv() => {
                    match event {
                        Ok(event @ ServerMessage::Event { sequence, .. }) if subscribed && sequence > subscription_floor => {
                            subscription_floor = sequence;
                            write_frame(&mut writer, &event).await?
                        },
                        Ok(_) => {},
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) if subscribed => {
                            let snapshot = state.snapshot();
                            if let ServerMessage::Snapshot { sequence, .. } = &snapshot { subscription_floor = *sequence; }
                            write_frame(&mut writer, &snapshot).await?
                        },
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {},
                        Err(tokio::sync::broadcast::error::RecvError::Closed) => return Ok(()),
                    }
                }
            }
        }
    }

    #[cfg(target_os = "linux")]
    fn verify_peer(stream: &UnixStream) -> anyhow::Result<()> {
        use nix::unistd::Uid;
        let peer = stream.peer_cred()?;
        anyhow::ensure!(
            peer.uid() == Uid::current().as_raw(),
            "IPC peer does not belong to the current user"
        );
        Ok(())
    }

    #[cfg(not(target_os = "linux"))]
    fn verify_peer(_stream: &UnixStream) -> anyhow::Result<()> {
        Ok(())
    }
}

#[cfg(windows)]
mod windows {
    pub async fn run() -> anyhow::Result<()> {
        anyhow::bail!(
            "the Windows named-pipe adapter is built only in the signed Windows target package"
        )
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "openwhisperd=info".into()),
        )
        .with_target(false)
        .compact()
        .init();
    #[cfg(unix)]
    return unix::run().await;
    #[cfg(windows)]
    return windows::run().await;
}
