# 1.0 implementation and release status

This branch is a tested foundation, not a stable product build. A command being
present does not imply its platform backend has passed release gates.

## Implemented and exercised

- Rust workspace, isolated paths/config/SQLite migrations, 30-day retention,
  text processing, 16 kHz mono normalization, privacy/provider policy, offline
  model verification/quarantine, capture generations, and clipboard ownership.
- Three `fast`, `balanced`, and `accurate` manifests are compiled in with
  commit-pinned HTTPS sources, exact size/hash/license/worker ABI,
  `builtin_pinned` trust, and
  non-gating `not_run` benchmark status. Explicit installs use Rustls, safe HTTPS
  redirects, disk preflight, private resumable ETag/Range staging, overflow
  bounds, fsync, quarantine, atomic rename, and SQLite registration. Offline
  import, verification, selection, and canonical-path-only removal are enabled.
- Private Unix IPC framing, maximum size, protocol N/N−1, Linux peer UID,
  single instance, structured errors, sequenced events, snapshots, and reconnect.
- Persistent bounded native-worker streaming protocol and supervisor timeout/
  one-restart/replay/stale-generation filtering. The worker pins `whisper-rs`
  0.16.0 with Linux Vulkan support, retains
  one whisper.cpp model context, validates 16 kHz mono PCM WAV, and supports
  auto/Arabic/English transcription, and reports requested/actual backend,
  device, and fallback. Explicit Vulkan fails closed; CPU disables GPU use.
  Local source builds can use the verified
  built-in manifest; public release channels may still adopt signed catalogs.
- Linux process-backed PipeWire/PulseAudio/ALSA capture, private session WAV
  staging, two-second stop escalation, startup cleanup, maximum-duration bounds,
  and Wayland/X11 clipboard delivery.
- Rolling 300 ms streaming, two-hypothesis stable word prefixes, bounded
  15-second coalescing, preview/commit events, final suffix flush, cross-chunk
  processing, safe X11 live insertion, cancellation, partial-result preservation,
  and 60-second result recovery.
- Bun CLI command tree, stable output/exit behavior, no-argument stream behavior,
  daemon auto-start/`--no-start`, and dynamically loaded OpenTUI operations board.
- Frozen golden contracts and raw benchmark metric harness.

## Stable blockers

- CoreAudio/WASAPI capture, native hotkey registration, Wayland/macOS/Windows
  insertion, secure stores, and native overlay backends. Linux currently uses a
  desktop-bound Alt+O Bun adapter and daemon-owned X11 insertion/notifications.
- Approved production catalog signature/public key and benchmark digest for
  published release channels, GPU artifacts, and one-hour/crash-loop validation.
- Rust cloud HTTP adapters and OS keychain implementations. Policy currently
  keeps every cloud provider disabled when no approved store is available.
- Windows named pipe current-user DACL and macOS audit-token peer verification.
- Idempotent service installers/uninstall purge, signed native packages, npm
  platform payload publication, SBOM/provenance/attestations, and rollback tests.
- Eight-target native runners, Arabic real-terminal human review, platform
  insertion matrix, 10,000-cycle reliability, and all published latency/RSS gates.
- Speaker-disjoint Arabic-English corpus results and confidence intervals. The
  built-in artifacts have publishable integrity hashes, but their benchmark
  status remains `not_run`; no competitor claim is authorized and
  benchmark results never gate local model readiness.
- Code-signing identities, personal npm scope, reverse-DNS identity validation,
  and OpenWhisper/OpenWhispr naming-confusion review.
