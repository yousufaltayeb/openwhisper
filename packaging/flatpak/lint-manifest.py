#!/usr/bin/env python3
"""Small dependency-free policy lint for the Flatpak manifest."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = Path(__file__).with_name("io.github.yousufaltayeb.OpenWhisper.yml")
METADATA = Path(__file__).with_name("flatpak-metadata")
ACCELERATOR_SOURCES = Path(__file__).with_name("accelerator-sources.json")
EXTENSION_MANIFESTS = {
    "nvidia": Path(__file__).with_name("openwhisper-nvidia-extension.yml"),
    "amd": Path(__file__).with_name("openwhisper-amd-extension.yml"),
}
FLATPAKREF = Path(__file__).with_name(
    "io.github.yousufaltayeb.OpenWhisper.flatpakref.in"
)
REQUIRED = {
    "app-id: io.github.yousufaltayeb.OpenWhisper",
    "runtime: org.gnome.Platform",
    'runtime-version: "50"',
    "sdk: org.gnome.Sdk",
    "org.freedesktop.Sdk.Extension.node24",
    "org.freedesktop.Sdk.Extension.rust-stable",
    "cargo --offline build --locked --release --features custom-protocol",
    "npm --prefix frontend ci --offline",
    "cargo-sources.json",
    "npm-sources.json",
    "metadata: flatpak-metadata",
    "OpenWhisper.Nvidia",
    "OpenWhisper.Amd",
    "OPENWHISPER_NVIDIA_EXTENSION",
    "OPENWHISPER_AMD_EXTENSION",
    "--share=ipc",
    "--socket=pulseaudio",
    "--socket=wayland",
    "--socket=fallback-x11",
    "--device=dri",
    "--talk-name=org.a11y.Bus",
    "--talk-name=org.freedesktop.portal.Desktop",
    "--talk-name=org.kde.StatusNotifierWatcher",
    "--filesystem=xdg-config/whisper:ro",
}
FORBIDDEN = (
    "--socket=session-bus",
    "--socket=system-bus",
    "--filesystem=host",
    # Flatpak only accepts bus-name wildcards ending in ``.*``. Tray items are
    # registered through StatusNotifierWatcher and need no own-name grant.
    "--own-name=org.kde.StatusNotifierItem-",
)
REQUIRED_FLATPAKREF = {
    "[Flatpak Ref]",
    "Name=io.github.yousufaltayeb.OpenWhisper",
    "Branch=@OPENWHISPER_BRANCH@",
    "GPGKey=@OPENWHISPER_GPG_PUBLIC_KEY_BASE64@",
}
FORBIDDEN_FLATPAKREF = ("Name=app/",)


def main() -> int:
    content = MANIFEST.read_text(encoding="utf-8")
    metadata = METADATA.read_text(encoding="utf-8")
    flatpakref = FLATPAKREF.read_text(encoding="utf-8")
    missing = sorted(value for value in REQUIRED if value not in content)
    unsafe = sorted(value for value in FORBIDDEN if value in content)
    missing_ref = sorted(
        value for value in REQUIRED_FLATPAKREF if value not in flatpakref
    )
    invalid_ref = sorted(
        value for value in FORBIDDEN_FLATPAKREF if value in flatpakref
    )
    metadata_errors = []
    if "[Application]" not in metadata:
        metadata_errors.append("flatpak metadata is missing its [Application] group")
    if "required-flatpak=1.18;" not in metadata:
        metadata_errors.append("flatpak metadata must require Flatpak 1.18")
    extension_errors = []
    for name, path in EXTENSION_MANIFESTS.items():
        extension = path.read_text(encoding="utf-8")
        required = [
            "build-extension: true",
            "base: io.github.yousufaltayeb.OpenWhisper",
            "extension-tag: " + name,
            "commit:" if name == "amd" else "sha256:",
        ]
        extension_errors.extend(
            f"{path.name}: missing {entry}" for entry in required if entry not in extension
        )
        if name == "amd" and "-DWITH_CUDA=ON" in extension:
            extension_errors.append("AMD extension enables the incompatible CUDA backend")
        if name == "amd":
            for entry in (
                "-DWITH_HIP=ON",
                "CTRANSLATE2_ROOT=/app",
                "./python",
                "pybind11-2.13.6.tar.gz",
            ):
                if entry not in extension:
                    extension_errors.append(
                        f"{path.name}: missing HIP Python binding input {entry}"
                    )
            if extension.find("- name: ctranslate2-rocm") < extension.find("- name: miopen"):
                extension_errors.append(
                    f"{path.name}: CTranslate2 must build after pinned ROCm libraries"
                )
        if name == "nvidia" and "WITH_HIP=ON" in extension:
            extension_errors.append("NVIDIA extension enables the incompatible HIP backend")
    try:
        accelerator_sources = json.loads(ACCELERATOR_SOURCES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        accelerator_sources = {}
        extension_errors.append(f"accelerator source lock is invalid: {exc}")
    if accelerator_sources.get("schema") != 1:
        extension_errors.append("accelerator source lock has an unsupported schema")
    malformed_commands = [
        line.strip()
        for line in content.splitlines()
        if line.lstrip().startswith("- python3 ") and ": " in line
    ]
    if (
        missing
        or unsafe
        or malformed_commands
        or missing_ref
        or invalid_ref
        or metadata_errors
        or extension_errors
    ):
        if missing:
            print("manifest missing required entries:", ", ".join(missing))
        if unsafe:
            print("manifest grants prohibited broad permissions:", ", ".join(unsafe))
        if malformed_commands:
            print(
                "manifest has unquoted YAML commands parsed as mappings:",
                ", ".join(malformed_commands),
            )
        if missing_ref:
            print("flatpakref missing required entries:", ", ".join(missing_ref))
        if invalid_ref:
            print("flatpakref has invalid Name syntax:", ", ".join(invalid_ref))
        if metadata_errors:
            print("flatpak metadata errors:", ", ".join(metadata_errors))
        if extension_errors:
            print("accelerator extension errors:", ", ".join(extension_errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
