# Archived pre-rewrite release checklist

> Archived on 2026-08-20. This checklist describes the obsolete
> Python/Tauri/Flatpak application and is not a release authority for the Rust
> daemon and OpenTUI application.

Use this checklist for every public release. A checked CI job is evidence, not
a substitute for checking the shipped artifact.

## Identity and versioning

- [ ] `pyproject.toml`, `src/openwhisper/__init__.py`, and release notes agree
  on the version.
- [ ] `frontend/package.json`, `src-tauri/Cargo.toml`, and `tauri.conf.json`
  agree with the Python version.
- [ ] Repository URLs point to `yousufaltayeb/openwhisper`.
- [ ] CLI is `openwhisper`; application ID is
  `io.github.yousufaltayeb.OpenWhisper`.
- [ ] No checkout path, username, UID, display, or machine-specific path is
  embedded in launchers, desktop files, documentation, or artifact metadata.

## Quality and privacy

- [ ] Run Python Ruff/tests/build, frontend tests/build/Impeccable detection,
  npm audit, Rust fmt/tests, and the feature-gated WDIO/axe WebKitGTK path under
  their locked Python 3.12, Node 24, and Cargo graphs.
- [ ] Inspect the production frontend and Tauri binary to confirm WDIO plugins,
  the global Tauri bridge, devtools, remote assets, and source maps are absent.
- [ ] Verify the NDJSON handshake, 8 MiB limit, allowlist, timeouts, idle-only
  restart, active-capture fatal latch, and three-second shutdown behavior.
- [ ] Exercise a local Faster Whisper dictation from recording through
  insertion; verify audio deletion afterward.
- [ ] Verify history retention and search with Arabic, English, and mixed text.
- [ ] Verify both shortcut modes and cancellation.
- [ ] On GNOME and KDE Wayland, verify four-argument portal signals, partial
  bindings, compositor reassignment, permission decline, portal restart,
  session closure, and push-to-talk release.
- [ ] Verify direct X11 insertion and a Wayland clipboard fallback. Confirm RTL
  insertion preserves Arabic text.
- [ ] Test each enabled cloud provider with mocked contract tests and a manual
  connection check using a non-production key.
- [ ] Search source, package contents, logs, and diagnostics for API keys,
  transcripts, authorization headers, and user-specific paths.
- [ ] Confirm no telemetry, analytics SDK, hosted account, or sync endpoint was
  introduced.

## Installation and distribution

- [ ] Run `python3 packaging/flatpak/lint-manifest.py`, validate AppStream
  metadata, and build the x86_64 Flatpak with `flatpak-builder --disable-download`.
- [ ] Verify only the documented network, PulseAudio, Wayland/fallback X11,
  DRI, AT-SPI, StatusNotifier, portal, and read-only legacy-config permissions
  are present. There must be no host filesystem or broad bus access.
- [ ] Verify the GNOME 50 WebKitGTK application, desktop entry, new icon, Tauri
  tray reachability fallback, Qt Multimedia capture/overlay, config creation,
  and one-time read-only legacy migration. Existing host files remain unchanged.
- [ ] Stop the cutover if GNOME or KDE cannot pass capture, insertion, overlay,
  and portal tests; do not remove the Qt parity shell before this gate.
- [ ] Install from the signed `.flatpakref`, reject a tampered repository
  summary, and prove an update from the prior beta revision.
- [ ] Verify model weights download only on explicit use; Faster Whisper is in
  the core runtime and optional Cohere local dependencies are in the signed
  extension.
- [ ] Publish static deltas plus `beta` or approved `stable` repository metadata
  to GitHub Pages. Keep the encrypted signing-key backup outside the repository.

## Legal and communication

- [ ] Review [NOTICE.md](../NOTICE.md), the exact dependency lock, model cards,
  and every bundled native library. Include required license texts and notices
  in the Flatpak and optional extension.
- [ ] Validate links and commands in [README.md](../README.md) and
  [README.ar.md](../README.ar.md).
- [ ] Ensure [CONTRIBUTING.md](../CONTRIBUTING.md), issue templates, roadmap,
  and support/release notes are current.
- [ ] Create the signed tag, GitHub release notes, artifact checksums, and
  source archive only after every applicable item is complete.
