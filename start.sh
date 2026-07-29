#!/usr/bin/env sh
# Source-development launcher. Packaged releases use the Flatpak desktop entry.

set -eu

if [ -f pyproject.toml ] && command -v uv >/dev/null 2>&1; then
    exec uv run openwhisper "$@"
fi

printf '%s\n' 'Run this from an OpenWhisper source checkout with uv installed.' >&2
exit 1
