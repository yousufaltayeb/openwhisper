# 1.0 implementation and release status

This branch is a tested foundation, not a stable product build. A command being
present does not imply its platform backend has passed release gates.

## Implemented and exercised

- Rust workspace, isolated paths/config/SQLite migrations, 30-day retention,
  text processing, 16 kHz mono normalization, privacy/provider policy, offline
  model verification/quarantine, capture generations, and clipboard ownership.
- Private Unix IPC framing, maximum size, protocol N/N−1, Linux peer UID,
  single instance, structured errors, sequenced events, snapshots, and reconnect.
- Persistent bounded native-worker protocol and supervisor timeout/restart/
  stale-generation filtering. The alpha worker intentionally refuses ASR until
  a verified whisper.cpp runtime/model catalog is linked.
- Bun CLI command tree, stable output/exit behavior, no-argument stream behavior,
  daemon auto-start/`--no-start`, and dynamically loaded OpenTUI operations board.
- Frozen golden contracts and raw benchmark metric harness.

## Stable blockers

- Real CoreAudio/WASAPI/PipeWire/Pulse/ALSA capture, hotkeys, focus tracking,
  insertion, secure stores, notifications, and all native overlay backends.
- Signed/pinned whisper.cpp and llama.cpp catalogs, resumable online downloads,
  CPU/Metal/CUDA/Vulkan artifacts, and one-hour/crash-loop validation.
- Rust cloud HTTP adapters and OS keychain implementations. Policy currently
  keeps every cloud provider disabled when no approved store is available.
- Windows named pipe current-user DACL and macOS audit-token peer verification.
- Idempotent service installers/uninstall purge, signed native packages, npm
  platform payload publication, SBOM/provenance/attestations, and rollback tests.
- Eight-target native runners, Arabic real-terminal human review, platform
  insertion matrix, 10,000-cycle reliability, and all published latency/RSS gates.
- Speaker-disjoint Arabic-English corpus results and confidence intervals. The
  `balanced` model remains a blocked release candidate with no publishable hash;
  no competitor claim is authorized.
- Code-signing identities, personal npm scope, reverse-DNS identity validation,
  and OpenWhisper/OpenWhispr naming-confusion review.
