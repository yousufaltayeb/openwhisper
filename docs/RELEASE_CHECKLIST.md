# OpenWhisper release checklist

Use this checklist for every public release. A checked CI job is evidence, not
a substitute for checking the shipped artifact.

## Identity and versioning

- [ ] `pyproject.toml`, `src/openwhisper/__init__.py`, and release notes agree
  on the version.
- [ ] Repository URLs point to `yousufaltayeb/openwhisper`.
- [ ] CLI is `openwhisper`; application ID is
  `io.github.yousufaltayeb.OpenWhisper`.
- [ ] No checkout path, username, UID, display, or machine-specific path is
  embedded in launchers, desktop files, documentation, or artifact metadata.

## Quality and privacy

- [ ] Run `uv run ruff check src tests`, `uv run pytest`, and `uv build` under
  Python 3.12.
- [ ] Exercise a local Faster Whisper dictation from recording through
  insertion; verify audio deletion afterward.
- [ ] Verify history retention and search with Arabic, English, and mixed text.
- [ ] Verify both shortcut modes and cancellation.
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
- [ ] Verify desktop entry, icon, Qt Multimedia microphone capture, config
  creation, and one-time read-only legacy migration. Existing host files must
  remain unchanged.
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
