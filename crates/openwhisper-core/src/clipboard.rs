use crate::DeliveryTarget;
use serde::{Deserialize, Serialize};
use std::process::Stdio;
use thiserror::Error;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClipboardWrite {
    pub previous_value: Option<String>,
    pub temporary_value: String,
    pub sequence_after_write: u64,
}

impl ClipboardWrite {
    pub fn can_restore(&self, current_sequence: u64, current_value: Option<&str>) -> bool {
        current_sequence == self.sequence_after_write
            && current_value == Some(self.temporary_value.as_str())
    }
}

#[derive(Debug, Error)]
pub enum ClipboardError {
    #[error("no Wayland or X11 clipboard command is available")]
    Unavailable,
    #[error("clipboard command failed: {0}")]
    Failed(String),
    #[error("clipboard I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("the insertion target changed")]
    TargetChanged,
    #[error("the X11 insertion adapter is unavailable")]
    InsertionUnavailable,
}

pub async fn capture_x11_target() -> Result<DeliveryTarget, ClipboardError> {
    if std::env::var_os("DISPLAY").is_none() || !command_exists("xdotool") {
        return Err(ClipboardError::InsertionUnavailable);
    }
    let window_id = command_output("xdotool", &["getactivewindow"]).await?;
    if window_id.is_empty() || !window_id.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(ClipboardError::InsertionUnavailable);
    }
    let application_id = command_output("xdotool", &["getwindowclassname", &window_id])
        .await
        .unwrap_or_default();
    let title_fingerprint = command_output("xdotool", &["getwindowname", &window_id])
        .await
        .unwrap_or_default();
    Ok(DeliveryTarget {
        application_id,
        window_id,
        title_fingerprint,
        captured_at: chrono::Utc::now(),
    })
}

pub async fn insert_x11_delta(target: &DeliveryTarget, text: &str) -> Result<(), ClipboardError> {
    insert_x11_text(target, text, true).await
}

pub async fn insert_x11_final_delta(
    target: &DeliveryTarget,
    text: &str,
) -> Result<(), ClipboardError> {
    insert_x11_text(target, text, false).await
}

async fn insert_x11_text(
    target: &DeliveryTarget,
    text: &str,
    restore_previous: bool,
) -> Result<(), ClipboardError> {
    let current = capture_x11_target().await?;
    if !target.still_matches(&current) {
        return Err(ClipboardError::TargetChanged);
    }
    let previous = read_text().await.ok();
    copy_text(text).await?;
    let mut owns_delta = false;
    for _ in 0..8 {
        if read_text().await.ok().as_deref() == Some(text) {
            owns_delta = true;
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    if !owns_delta {
        return Err(ClipboardError::Failed(
            "the session clipboard did not acquire the committed delta".into(),
        ));
    }
    let paste_key = if is_terminal_class(&target.application_id) {
        "ctrl+shift+v"
    } else {
        "ctrl+v"
    };
    let status = Command::new("xdotool")
        .args(["key", "--clearmodifiers", paste_key])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await?;
    if !status.success() {
        return Err(ClipboardError::InsertionUnavailable);
    }
    // X11 key delivery is asynchronous, and some terminals consume the
    // selection well after xdotool returns. Restore in the background only if
    // this exact delta still owns the clipboard. A newer delta or a user copy
    // therefore wins without blocking transcription.
    if restore_previous && let Some(previous) = previous {
        let temporary = text.to_owned();
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            if read_text().await.ok().as_deref() == Some(&temporary) {
                let _ = copy_text(&previous).await;
            }
        });
    }
    Ok(())
}

pub async fn read_text() -> Result<String, ClipboardError> {
    let candidates: &[(&str, &[&str])] = if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        &[("wl-paste", &["--no-newline"])]
    } else if std::env::var_os("DISPLAY").is_some() {
        &[
            ("xclip", &["-selection", "clipboard", "-out"]),
            ("xsel", &["--clipboard", "--output"]),
        ]
    } else {
        &[]
    };
    read_candidates(candidates).await
}

async fn read_candidates(candidates: &[(&str, &[&str])]) -> Result<String, ClipboardError> {
    for (command, args) in candidates {
        if !command_exists(command) {
            continue;
        }
        let output = Command::new(command)
            .args(*args)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .await?;
        if output.status.success() {
            return String::from_utf8(output.stdout)
                .map_err(|error| ClipboardError::Failed(error.to_string()));
        }
    }
    Err(ClipboardError::Unavailable)
}

async fn command_output(command: &str, args: &[&str]) -> Result<String, ClipboardError> {
    let output = Command::new(command)
        .args(args)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .await?;
    if !output.status.success() {
        return Err(ClipboardError::Failed(format!(
            "{command} exited unsuccessfully"
        )));
    }
    String::from_utf8(output.stdout)
        .map(|value| value.trim().to_owned())
        .map_err(|error| ClipboardError::Failed(error.to_string()))
}

fn is_terminal_class(value: &str) -> bool {
    [
        "ghostty",
        "alacritty",
        "kitty",
        "wezterm",
        "gnome-terminal",
        "konsole",
        "xfce4-terminal",
        "xterm",
    ]
    .iter()
    .any(|candidate| value.to_ascii_lowercase().contains(candidate))
}

pub async fn copy_text(text: &str) -> Result<&'static str, ClipboardError> {
    let candidates: &[(&str, &[&str])] = if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        &[("wl-copy", &[])]
    } else if std::env::var_os("DISPLAY").is_some() {
        &[
            ("xclip", &["-selection", "clipboard"]),
            ("xsel", &["--clipboard", "--input"]),
        ]
    } else {
        &[]
    };
    copy_candidates(text, candidates).await
}

async fn copy_candidates(
    text: &str,
    candidates: &[(&'static str, &[&str])],
) -> Result<&'static str, ClipboardError> {
    for (command, args) in candidates {
        if !command_exists(command) {
            continue;
        }
        let mut child = Command::new(command)
            .args(*args)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()?;
        let mut input = child
            .stdin
            .take()
            .ok_or_else(|| ClipboardError::Failed("stdin was unavailable".into()))?;
        input.write_all(text.as_bytes()).await?;
        drop(input);
        if child.wait().await?.success() {
            return Ok(command);
        }
    }
    Err(ClipboardError::Unavailable)
}

fn command_exists(command: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|paths| {
        std::env::split_paths(&paths).any(|directory| directory.join(command).is_file())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restores_only_when_openwhisper_still_owns_clipboard() {
        let write = ClipboardWrite {
            previous_value: Some("before".into()),
            temporary_value: "dictated".into(),
            sequence_after_write: 9,
        };
        assert!(write.can_restore(9, Some("dictated")));
        assert!(!write.can_restore(10, Some("dictated")));
        assert!(!write.can_restore(9, Some("user copied this")));
    }
}
