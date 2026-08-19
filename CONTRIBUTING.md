# Contributing to OpenWhisper

Thanks for helping make private Linux dictation dependable, especially for
Arabic and Arabic-English writing. OpenWhisper is in its v0.1 alpha phase, so
small reproducible fixes, tests, accessibility improvements, and documentation
corrections are all valuable.

## Before you start

- Search existing issues and pull requests before opening a new one.
- Discuss a substantial feature or provider integration in an issue first.
- Do not include API keys, audio recordings, full transcripts, local database
  files, screenshots containing private text, or personally identifying logs.
- Keep a pull request focused. Separate refactors from behavior changes when
  possible.

## Development setup

OpenWhisper requires Python 3.12, uv, Node 24/npm 11, and stable Rust. Create
the Python environment and install the locked frontend graph:

```bash
uv python install 3.12
uv sync --extra dev
npm --prefix frontend ci
```

Start the app with:

```bash
npm run tauri:dev
```

`uv run openwhisper` is the temporary Qt parity shell. The shipping milestone
uses the Tauri host and the private `openwhisper-engine` child process.

For cloud-provider work, install the extras only when needed:

```bash
uv sync --extra dev --extra cloud
```

API keys belong in the Secret-portal encrypted store or short-lived environment
variables, never in `config.ini`, fixtures, commits, issues, or pull-request text.

## Checks

Run the relevant checks before requesting review:

```bash
uv run ruff check src tests
uv run pytest
uv build
npm run frontend:test
npm run frontend:build
npm --prefix frontend audit
npx --yes impeccable@3.5.0 detect --json frontend/src
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo test --locked --all-features --manifest-path src-tauri/Cargo.toml
npm run e2e:build
npm run e2e
sh -n start.sh
sh -n runit/run
python3 packaging/flatpak/lint-manifest.py
```

Run focused tests while iterating, then the complete suite before merging. New
behavior should have a unit test at the lowest practical boundary. Provider
adapters must test success, missing credentials, authentication failures, rate
limits, timeouts, invalid audio, and malformed responses without reaching a
live service.

The E2E build runs axe and Capture interactions in the real Linux WebKitGTK
Tauri window. Its WDIO Rust plugins and global Tauri bridge are enabled only by
the `e2e` Cargo feature and `tauri.e2e.conf.json`; never enable either in a
release build.

## Design boundaries

- Keep UI, session orchestration, audio, insertion, history, and provider
  adapters independently testable.
- Do not make providers depend on PySide6 widgets.
- Do not log transcript content, request bodies, secrets, or authorization
  headers. Error messages shown to users must also be safe to copy into a bug
  report.
- Preserve Arabic text, right-to-left text, punctuation, and Arabic-English
  code-switching. Avoid cleanup rules that silently rewrite a raw transcript.
- Treat direct text insertion as best effort. Clipboard fallback must preserve
  the text and make the fallback clear to the user.
- Never delete user data outside OpenWhisper-owned temporary audio files.

## Pull requests

Use an imperative title and describe the user-visible behavior, test coverage,
and any platform limitation. Attach redacted screenshots for UI changes. A
maintainer may ask for a changelog note or a separate release-note entry.

By contributing, you agree that your contribution is available under the
repository's [MIT License](LICENSE). Preserve third-party notices when copying
or distributing upstream code or dependencies; see [NOTICE.md](NOTICE.md).
