# Pre-CLI rewrite baseline

The Python/Tauri migration was frozen on 2026-08-19 at commit `d05b851` on
branch `archive/pre-cli-rewrite-2026-08-19`.

Verified before archival:

- Python: `uv run pytest -q` — 156 passed.
- Rust/Tauri: `cargo test --manifest-path src-tauri/Cargo.toml --quiet` — 17 passed.
- React: `npm --prefix frontend test -- --run` — 23 passed.

Local agent state (`.agents`, `.codex`, `.impeccable`), caches, credentials,
model weights, and generated build output were excluded. The archived code is a
behavioral reference; it is not read or invoked by the 1.0 daemon.
