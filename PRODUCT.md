# Product

<!-- impeccable:product-schema 1 -->

## Platform

Cross-platform terminal client, per-user native daemon, and native background
identity for macOS, Windows, and Linux.

## Stack

Rust owns domain state, private IPC, persistence, workers, platform adapters,
and the overlay. Strict TypeScript compiled by Bun owns the command parser and
OpenTUI presentation. The base application requires neither Node, Bun, Python,
Tauri, nor Qt after compilation. Python exists only in an isolated optional
experimental model pack.

## Users

Bilingual developers and power users who dictate Arabic, English, technical
terms, paths, flags, and source code across applications. They need capture to
survive a closed terminal and need reliable noninteractive JSON commands for
automation, headless machines, SSH, and containers.

## Product purpose

OpenWhisper provides local-first system-wide dictation. A successful session
starts from a hotkey or CLI, makes capture state unmistakable, transcribes with
a verified local model or explicitly configured BYOK provider, applies only the
selected transformations, retains searchable history under the user's policy,
and inserts only into the still-safe capture target. Unsafe delivery copies and
notifies instead.

## Positioning

OpenWhisper is open, local-first, cross-platform, and built to be benchmarked
for Arabic-English speech. Competitive claims require reproducible public
results and confidence intervals; no unverified superiority copy is permitted.

## Operating context

The daemon remains alive when every terminal closes. The TUI is one replaceable
client over private local IPC. Desktop capability varies honestly by OS/session;
Tier 3 headless environments guarantee file/stdin transcription and JSON CLI,
not microphone hotkeys, overlay, or arbitrary insertion.

## Capabilities and constraints

- The daemon is the only state writer and owns recording, focus retention,
  insertion, clipboard recovery, credentials, notifications, and autostart.
- Microphone audio never enters public client IPC. Workers use separate bounded
  inherited channels with cancellation, timeout, restart, and generation checks.
- New versioned `config.toml` and `state.sqlite3` never read, migrate, alter, or
  delete legacy INI/history/personalization/credential/model data.
- No model ships in the base application. Downloads, cloud calls, cleanup
  uploads, and update checks require explicit user action.
- No telemetry, analytics, automatic crash upload, transcript-bearing logs,
  runtime CDN, postinstall download, or system-Python mutation.
- Arabic stays in logical NFC Unicode order. Any terminal BiDi compatibility is
  view-only; copied, exported, JSON, and database text remains byte-accurate.
- Stable is blocked on signed binaries, all eight native target runners, real
  Arabic terminal review, reliability/performance gates, and naming/identity
  approvals.

## Brand commitments

Keep the OpenWhisper name and a restrained precision-audio identity: matte
graphite rails, chalk work surfaces, one recording signal, exact spacing,
limited elevation, and functional measurement. Readex Pro remains the
Arabic/Latin display face where fonts exist; IBM Plex Mono is for machine state.
In terminals, use native cell text and measured rows rather than pretending
font control. No gradients, glass, decorative glow, generic dashboard cards,
or invented benchmarks.

## Product principles

- Keep speech private by construction, not reassurance.
- Make unavailable capabilities and recovery actions explicit.
- Treat Arabic, English, and mixed-direction content as equally real input.
- Keep every TUI action available as a noninteractive command.
- Fail closed on network access, credential protection, focus safety, and model
  verification.
- Do not call scaffolding or cross-compilation “platform support.”

## Accessibility and inclusion

The TUI supports full keyboard control, small terminals, plain output, `NO_COLOR`,
redirected streams, terminal restoration, and selectable text. Status is always
named in text rather than color alone. Native installers and overlay adapters
must respect OS accessibility and reduced-motion settings.
