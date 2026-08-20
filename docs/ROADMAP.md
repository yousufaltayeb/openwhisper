# Archived pre-rewrite roadmap

> Archived on 2026-08-20. This Python/Tauri/Flatpak roadmap is historical and
> must not be used to scope or package the Rust daemon and OpenTUI application.

This roadmap describes intended scope, not a promise of delivery dates. Release
work is complete only when the corresponding release checklist passes.

## v0.1 — private Linux dictation

- [ ] Pass the React/Tauri hybrid spike—engine IPC, Capture, tray, shortcuts,
  microphone, insertion, and retained PySide overlay—on GNOME and KDE Wayland.
- [ ] Port Library, Personalize, System, onboarding, and the command drawer only
  after that spike passes; keep the Qt main window as a development parity shell.
- [ ] Toggle and push-to-talk shortcuts.
- [ ] Faster Whisper local transcription as the default path, with on-demand
  model downloads and CPU/CUDA selection.
- [ ] Arabic, Arabic dialect, English, and Arabic-English code-switching
  workflows covered by tests and manual acceptance checks.
- [ ] Opt-in Raw, Clean, Formal/MSA, and Custom cleanup modes with a safe raw
  fallback.
- [ ] BYOK Cohere, OpenAI, Groq, and Deepgram adapters, with explicit provider
  connection testing and secret redaction.
- [ ] XDG Secret-portal encrypted credential storage with environment/session
  fallback when the portal is unavailable.
- [ ] Local history retention controls, audio deletion, stale-temp cleanup, and
  no telemetry.
- [ ] Global Shortcuts portal with an X11 fallback, AT-SPI context/insertion
  safeguards, and a clear Wayland clipboard fallback.
- [ ] Signed x86_64 Flatpak beta/stable remotes on GitHub Pages, static deltas,
  an optional signed local-model extension, and no legacy package or local installer.
- [ ] English and Arabic documentation, contribution guide, notices, issue
  templates, and a tagged release checklist.

## After v0.1

- Improve desktop-specific Wayland insertion where secure compositor APIs allow
  it.
- Broaden provider/model options only when their privacy and error behavior can
  meet the same contract as the local path.
- Evaluate packaging for additional Linux architectures after reproducible
  testing is available.

## Explicitly out of scope for v0.1

macOS, Windows, mobile clients, meeting recording, speaker diarization,
accounts, cloud sync, hosted OpenWhisper services, and non-x86_64 artifacts.
