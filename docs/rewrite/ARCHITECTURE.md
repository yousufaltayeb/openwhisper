# OpenWhisper 1.0 architecture

```text
openwhisper (Bun/OpenTUI) ── private versioned IPC ── openwhisperd (Rust)
                                                       ├─ state.sqlite3 writer
                                                       ├─ capture/delivery policy
                                                       ├─ supervised ASR worker
                                                       └─ overlay subscriber
```

The daemon is the only durable-state writer and the only owner of recording,
focus retention, insertion, clipboard recovery, credentials, and notifications.
The CLI/TUI is replaceable presentation. Microphone audio is normalized to
16 kHz mono PCM and travels only over bounded inherited worker channels, never
over client IPC.

IPC uses a 4-byte big-endian length followed by UTF-8 JSON with an 8 MiB hard
limit. Protocol 2 accepts clients speaking 2 or 1. Unix sockets live in a 0700
runtime directory, have mode 0600, and Linux verifies the peer UID. The Windows
adapter must use a named pipe with a current-user DACL; no TCP listener is
permitted.

Canonical messages live in
[`schemas/protocol/openwhisper.schema.json`](../../schemas/protocol/openwhisper.schema.json).
Checked-in Rust and TypeScript bindings carry its SHA-256 and are synchronized
with `npm run rewrite:protocol`.

The state flow is capture → transcription → deterministic/optional cleanup →
vocabulary/replacements → history → safe insertion → notification. Generation
IDs discard late worker results. The capture-start target must still match at
delivery. Clipboard restoration is allowed only while both its sequence and
temporary value still match OpenWhisper's write.
