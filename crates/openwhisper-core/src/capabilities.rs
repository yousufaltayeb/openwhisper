use openwhisper_protocol::{Capabilities, Capability};

#[cfg(target_os = "linux")]
fn command_exists(command: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|paths| {
        std::env::split_paths(&paths).any(|directory| directory.join(command).is_file())
    })
}

fn available(backend: &str, detail: &str) -> Capability {
    Capability {
        available: true,
        backend: backend.into(),
        detail: detail.into(),
        fallback: None,
    }
}

fn unavailable(backend: &str, detail: &str, fallback: &str) -> Capability {
    Capability {
        available: false,
        backend: backend.into(),
        detail: detail.into(),
        fallback: Some(fallback.into()),
    }
}

pub fn detect_capabilities() -> Capabilities {
    #[cfg(target_os = "linux")]
    {
        let session = std::env::var("XDG_SESSION_TYPE").unwrap_or_else(|_| "unknown".into());
        let desktop = std::env::var("XDG_CURRENT_DESKTOP")
            .unwrap_or_default()
            .to_lowercase();
        let wayland = session == "wayland";
        let x11 = std::env::var_os("DISPLAY").is_some();
        let session_bus = std::env::var_os("DBUS_SESSION_BUS_ADDRESS").is_some();
        let wlroots = std::env::var_os("SWAYSOCK").is_some()
            || std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE").is_some();
        let audio_backend = ["pw-record", "parec", "arecord"]
            .into_iter()
            .find(|command| command_exists(command));
        Capabilities {
            audio: audio_backend.map_or_else(
                || {
                    unavailable(
                        "none",
                        "No PipeWire, PulseAudio, or ALSA capture tool was found.",
                        "File/stdin transcription remains available.",
                    )
                },
                |backend| {
                    available(
                        backend,
                        "The process-backed 16 kHz mono capture adapter is available.",
                    )
                },
            ),
            toggle_hotkey: if x11 || (wayland && session_bus) {
                unavailable(
                    if wayland { "portal" } else { "x11" },
                    "A session backend is present, but the alpha hotkey adapter is not linked.",
                    "Use explicit CLI commands.",
                )
            } else {
                unavailable(
                    "none",
                    "No graphical session control backend was detected.",
                    "Use explicit CLI record commands.",
                )
            },
            push_to_talk: if wayland {
                unavailable(
                    "portal",
                    "Release events are compositor-dependent.",
                    "Use toggle mode.",
                )
            } else if x11 {
                unavailable(
                    "x11",
                    "Press/release events exist, but the alpha PTT adapter is not linked.",
                    "Use explicit CLI commands.",
                )
            } else {
                unavailable(
                    "none",
                    "No press/release event backend was detected.",
                    "Use toggle or explicit CLI commands.",
                )
            },
            insertion: if wayland {
                unavailable(
                    "at-spi",
                    "Arbitrary Wayland insertion is session-dependent.",
                    "Copy to clipboard and notify.",
                )
            } else if x11 {
                unavailable(
                    "at-spi/x11",
                    "A target backend exists, but the alpha insertion adapter is not linked.",
                    "Print command output explicitly.",
                )
            } else {
                unavailable(
                    "none",
                    "No graphical target session was detected.",
                    "Print or copy command output explicitly.",
                )
            },
            overlay: if x11 || wlroots {
                unavailable(
                    if wayland {
                        "wlr-layer-shell"
                    } else {
                        "x11-override-redirect"
                    },
                    "A window backend exists, but the alpha overlay adapter is not linked.",
                    "Use TUI state and sound/notification fallback after those adapters pass.",
                )
            } else {
                unavailable(
                    if desktop.contains("gnome") {
                        "gnome-wayland"
                    } else {
                        "wayland"
                    },
                    "Layer-shell is unavailable.",
                    "Use sounds and desktop notifications.",
                )
            },
            notifications: if session_bus {
                unavailable(
                    "freedesktop",
                    "A desktop session bus is present, but the alpha notification adapter is not linked.",
                    "Observe CLI/TUI status output.",
                )
            } else {
                unavailable(
                    "none",
                    "No desktop session bus was detected.",
                    "Observe CLI/TUI status output.",
                )
            },
            secrets: if session_bus && command_exists("secret-tool") {
                unavailable(
                    "secret-service",
                    "Secret Service is present, but the alpha credential adapter is not linked.",
                    "Cloud providers remain disabled.",
                )
            } else {
                unavailable(
                    "secret-service",
                    "No usable Secret Service client was detected.",
                    "Cloud providers stay disabled unless a passphrase-encrypted fallback is explicitly configured.",
                )
            },
            service_manager: if command_exists("systemctl") {
                unavailable(
                    "systemd-user",
                    "systemctl is present, but setup is not linked in this alpha build.",
                    "Use foreground development mode.",
                )
            } else {
                unavailable(
                    "xdg-autostart",
                    "systemd was not detected.",
                    "Use foreground development mode.",
                )
            },
            accelerator: available(
                "cpu",
                "The native whisper.cpp CPU backend is linked; model integrity is reported separately by readiness diagnostics.",
            ),
        }
    }

    #[cfg(target_os = "macos")]
    {
        return Capabilities {
            audio: unavailable(
                "coreaudio",
                "The CoreAudio capture adapter is not implemented in this Linux slice.",
                "Use Linux file/stdin transcription.",
            ),
            toggle_hotkey: unavailable(
                "carbon",
                "Not implemented in this slice.",
                "Use CLI commands.",
            ),
            push_to_talk: unavailable(
                "event-tap",
                "Not implemented in this slice.",
                "Use toggle commands.",
            ),
            insertion: unavailable(
                "accessibility",
                "Not implemented in this slice.",
                "Use printed output.",
            ),
            overlay: unavailable(
                "appkit-panel",
                "Not implemented in this slice.",
                "Use TUI status.",
            ),
            notifications: unavailable(
                "usernotifications",
                "Not implemented in this slice.",
                "Use TUI status.",
            ),
            secrets: unavailable(
                "keychain",
                "Not implemented in this slice.",
                "Cloud providers remain disabled.",
            ),
            service_manager: unavailable(
                "launchagent",
                "Not implemented in this slice.",
                "Run foreground development mode.",
            ),
            accelerator: unavailable(
                "cpu",
                "The native worker package is not approved on macOS.",
                "Use Linux.",
            ),
        };
    }

    #[cfg(target_os = "windows")]
    {
        return Capabilities {
            audio: unavailable(
                "wasapi",
                "The WASAPI adapter is not implemented in this Linux slice.",
                "Use Linux file/stdin transcription.",
            ),
            toggle_hotkey: unavailable(
                "registerhotkey",
                "Not implemented in this slice.",
                "Use CLI commands.",
            ),
            push_to_talk: unavailable(
                "low-level-hook",
                "Not implemented in this slice.",
                "Use toggle commands.",
            ),
            insertion: unavailable(
                "uia/sendinput",
                "Not implemented in this slice.",
                "Use printed output.",
            ),
            overlay: unavailable(
                "win32-layered",
                "Not implemented in this slice.",
                "Use TUI status.",
            ),
            notifications: unavailable(
                "windows-app-sdk",
                "Not implemented in this slice.",
                "Use TUI status.",
            ),
            secrets: unavailable(
                "credential-manager",
                "Not implemented in this slice.",
                "Cloud providers remain disabled.",
            ),
            service_manager: unavailable(
                "startup-task",
                "Not implemented in this slice.",
                "Run foreground development mode.",
            ),
            accelerator: unavailable(
                "cpu",
                "The native worker package is not approved on Windows.",
                "Use Linux.",
            ),
        };
    }
}
