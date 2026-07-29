#!/usr/bin/env python3
"""Small dependency-free policy lint for the Flatpak manifest."""

from __future__ import annotations

from pathlib import Path

MANIFEST = Path(__file__).with_name("io.github.yousufaltayeb.OpenWhisper.yml")
REQUIRED = {
    "app-id: io.github.yousufaltayeb.OpenWhisper",
    "runtime: org.kde.Platform",
    'runtime-version: "6.11"',
    "--share=ipc",
    "--socket=pulseaudio",
    "--socket=wayland",
    "--socket=fallback-x11",
    "--device=dri",
    "--talk-name=org.a11y.Bus",
    "--talk-name=org.freedesktop.portal.Desktop",
    "--own-name=org.kde.StatusNotifierItem-*",
    "--talk-name=org.kde.StatusNotifierWatcher",
    "--filesystem=xdg-config/whisper:ro",
}
FORBIDDEN = ("--socket=session-bus", "--socket=system-bus", "--filesystem=host")


def main() -> int:
    content = MANIFEST.read_text(encoding="utf-8")
    missing = sorted(value for value in REQUIRED if value not in content)
    unsafe = sorted(value for value in FORBIDDEN if value in content)
    if missing or unsafe:
        if missing:
            print("manifest missing required entries:", ", ".join(missing))
        if unsafe:
            print("manifest grants prohibited broad permissions:", ", ".join(unsafe))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
