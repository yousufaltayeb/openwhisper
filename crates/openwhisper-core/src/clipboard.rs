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
