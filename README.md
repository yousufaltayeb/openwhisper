# OpenWhisper

OpenWhisper is an open, local-first, cross-platform dictation system for Arabic,
English, and developer code-switching. The 1.0 rewrite is TUI-first: a Rust
per-user daemon owns recording and private state while a Bun-compiled OpenTUI
client provides interactive and scriptable control.

> **Alpha foundation:** the daemon, IPC, state, CLI, TUI, worker supervision,
> privacy gates, built-in pinned `balanced` model path, and test contracts run
> today. Cross-platform audio/insertion/overlay, cloud transports, signed
> installers, and the eight-target
> acceptance matrix remain release blockers. See
> [1.0 release status](docs/rewrite/RELEASE_STATUS.md). Do not use this branch to
> make competitive performance claims.

```text
openwhisper                Bun/OpenTUI client
      │ private protocol 3/2 IPC (never microphone audio)
openwhisperd               Rust per-user daemon and sole state writer
      ├─ persistent CPU whisper.cpp worker (whisper-rs 0.16.0)
      └─ stateless native overlay subscriber
```

## CLI contract

Running `openwhisper` in a terminal opens the TUI. With redirected input/output,
it prints help without starting the daemon or terminal renderer.

```text
openwhisper [ui]
openwhisper record start|stop|toggle|cancel|status [--wait]
openwhisper transcribe <path|-> [--mode raw|clean|code] [--insert]
openwhisper history list|search|show|copy|delete|clear|export
openwhisper modes list|show|select
openwhisper vocab list|add|remove|import|export
openwhisper snippets list|add|remove|run|import|export
openwhisper models list|install|remove|verify|select|import
openwhisper providers list|configure|test|unset
openwhisper config list|get|set
openwhisper service install|start|stop|restart|status|uninstall
openwhisper setup | doctor | logs | completion | update | version
```

Result data goes to stdout and diagnostics to stderr. `--plain`, `--json`,
`--jsonl`, `--no-color`, `--no-start`, and `NO_COLOR` are consistent across
commands. Exit codes are stable: 0 success, 2 usage/configuration, 3 daemon
unavailable, 4 unsupported/permission, 5 model/provider unavailable,
6 transcription/cleanup, 7 insertion, 8 network/I/O, and 130 cancellation.

## Build and verify

Development currently requires stable Rust, Bun 1.3.14, Node/npm for the
archived frontend tests, Python 3.12/uv for the behavioral reference, and no
system Python at runtime for the new application.

```bash
bun --cwd cli install --frozen-lockfile
npm run rewrite:protocol
npm run rewrite:check
npm run rewrite:build
```

For a development smoke test:

```bash
npm run rewrite:build:dev
./target/debug/openwhisper service start
./target/debug/openwhisper models install balanced
./target/debug/openwhisper doctor --json
./target/debug/openwhisper service stop
```

No speech model is bundled and no model is fetched during build, startup, TUI
launch, or diagnostics. An explicit `models install balanced` command can fetch
the source-build model after interactive confirmation (or `--yes`). The daemon
pins `large-v3-turbo-q5_0` to Hugging Face commit
`98aa99a0a9db05ae2342309f5096248665f7cba3`, requires exactly `574041195`
bytes and SHA-256
`394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2`,
uses the MIT license and `openwhisper-worker-1` ABI, and reports trust as
`builtin_pinned`. Downloads are private, resumable, disk-preflighted, verified,
and atomically registered. Use `models import balanced <path>` for a completely
offline install through the same checks. Benchmark status is honestly reported
as `not_run` and does not gate local dictation.

## Privacy and data

The 1.0 daemon creates a new versioned `config.toml` and `state.sqlite3` with
private per-user permissions. It never opens, migrates, edits, or deletes old
INI files, history, personalization, credentials, or CTranslate2/model caches.
`doctor` may report that legacy paths exist by checking metadata only. Uninstall
preserves data unless a signed installer implements and the user explicitly
requests `--purge`; legacy data is never a purge target.

Cloud and update network access require explicit user action. Local-only policy
fails closed. Cloud providers remain disabled without Keychain, Windows
Credential Manager, Linux Secret Service, or an explicitly enabled
passphrase-encrypted fallback. Logs must never contain transcripts, audio,
clipboard contents, API keys, or request parameters. There is no telemetry,
analytics, or automatic crash upload.

Read the [architecture](docs/rewrite/ARCHITECTURE.md),
[data boundary](docs/rewrite/DATA_BOUNDARY.md), and
[archived baseline](docs/rewrite/BASELINE.md) before contributing to the rewrite.

## Benchmarks

The frozen harness reports WER, Arabic CER, mixed error rate, technical-term
recall, and named-entity recall from raw prediction JSONL. The included fixture
tests metric plumbing only:

```bash
npm run benchmark:fixture
```

Publishable results require speaker-disjoint corpora, immutable model/settings/
hardware manifests, raw predictions, and bootstrap confidence intervals. A
“better Arabic-English code-switching” claim is forbidden until the documented
release gate is met reproducibly.

After local dictation works, `npm run benchmark:compare -- ...` can run the
native worker and archived Faster Whisper provider over the same deterministic
600-item Perle split. It writes raw, reproducible, non-gating evidence only to
the gitignored `benchmarks/local-results/` directory and never publishes or
changes readiness automatically.

## License and provenance

OpenWhisper is MIT-licensed and retains the upstream Soupawhisper notices. See
[LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). The pre-rewrite Python/Tauri app
remains available on `archive/pre-cli-rewrite-2026-08-19` at `d05b851` as a
behavioral reference.
