#!/usr/bin/env python3
raise SystemExit("Archived pre-rewrite Flatpak workflow; refusing to prepare an obsolete package")

"""Create the pinned offline wheelhouse used by Flatpak builds.

Run this only in a controlled release-preparation environment. The resulting
wheelhouse is an input to the offline builder; CI never asks pip to resolve or
download dependencies while it builds the application.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging" / "flatpak"
WHEELS = PACKAGING / "wheels"
ACCELERATOR_SOURCES = PACKAGING / "accelerator-sources.json"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
FLATPAK_PYTHON = (3, 13)
SETUPTOOLS_VERSION = "83.0.0"
BUILDER_TOOLS_COMMIT = "737c0085912f9f7dabf9341d4608e2a77a51a73a"


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def build_source_wheels() -> None:
    """Turn locked source distributions into target-ABI wheels before the build."""
    source_archives = sorted(WHEELS.glob("*.tar.gz"))
    if not source_archives:
        return
    with tempfile.TemporaryDirectory(prefix="openwhisper-wheel-builder-") as temporary:
        builder_python = Path(temporary) / "bin" / "python"
        run(sys.executable, "-m", "venv", temporary)
        run(
            str(builder_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            f"--find-links={WHEELS}",
            f"setuptools=={SETUPTOOLS_VERSION}",
        )
        for archive in source_archives:
            run(
                str(builder_python),
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-index",
                f"--find-links={WHEELS}",
                f"--wheel-dir={WHEELS}",
                str(archive),
            )
            archive.unlink()


def build_application_wheel() -> None:
    """Build OpenWhisper before the Flatpak sandbox enters offline mode."""
    run("uv", "build", "--wheel", "--out-dir", str(WHEELS))
    # uv marks an in-tree output directory as ignored. The release cache gate
    # intentionally permits wheel artifacts only.
    (WHEELS / ".gitignore").unlink(missing_ok=True)


def generate_desktop_sources() -> None:
    """Pin npm and Cargo archives for a fully offline Flatpak build."""
    with tempfile.TemporaryDirectory(prefix="openwhisper-flatpak-tools-") as temporary:
        temporary_path = Path(temporary)
        tools = temporary_path / "flatpak-builder-tools"
        run(
            "git",
            "clone",
            "--no-checkout",
            "https://github.com/flatpak/flatpak-builder-tools.git",
            str(tools),
        )
        run("git", "-C", str(tools), "checkout", "--detach", BUILDER_TOOLS_COMMIT)
        run(
            "uv",
            "run",
            "--with",
            "aiohttp",
            "--with",
            "tomlkit",
            "python",
            str(tools / "cargo" / "flatpak-cargo-generator.py"),
            str(ROOT / "src-tauri" / "Cargo.lock"),
            "-o",
            str(PACKAGING / "cargo-sources.json"),
        )
        frontend_lock = temporary_path / "frontend"
        frontend_lock.mkdir()
        shutil.copy2(ROOT / "frontend" / "package.json", frontend_lock)
        shutil.copy2(ROOT / "frontend" / "package-lock.json", frontend_lock)
        run(
            "uv",
            "tool",
            "run",
            "--from",
            str(tools / "node"),
            "flatpak-node-generator",
            "npm",
            str(frontend_lock / "package-lock.json"),
            "--no-requests-cache",
            "-o",
            str(PACKAGING / "npm-sources.json"),
        )


def validate_accelerator_sources() -> None:
    """Fail release preparation if optional accelerator pins are malformed.

    Accelerator archives are cached by their dedicated extension jobs. The
    core CPU preparation still validates the shared lock so a release cannot
    accidentally publish an extension manifest with an unreviewed floating
    source.
    """
    try:
        lock = json.loads(ACCELERATOR_SOURCES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid accelerator source lock: {exc}") from exc
    if lock.get("schema") != 1:
        raise SystemExit("unsupported accelerator source lock schema")
    for name, entry in lock.get("nvidia", {}).items():
        if isinstance(entry, dict) and not entry.get("sha256"):
            raise SystemExit(f"NVIDIA source {name} is missing a sha256 pin")
    amd = lock.get("amd", {})
    for key in (
        "rocmCoreCommit",
        "hipCommit",
        "rocblasCommit",
        "hipblasCommit",
        "miopenCommit",
    ):
        if not isinstance(amd.get(key), str) or len(amd[key]) != 40:
            raise SystemExit(f"AMD source {key} is missing a full git commit")


def main() -> int:
    running_python = sys.version_info[:2]
    if running_python != FLATPAK_PYTHON:
        expected = ".".join(map(str, FLATPAK_PYTHON))
        actual = ".".join(map(str, running_python))
        raise SystemExit(
            f"Flatpak wheels must be prepared with Python {expected} to match "
            f"org.gnome.Sdk//50 (running Python {actual})"
        )
    if shutil.which("uv") is None:
        raise SystemExit("uv is required to export the locked Flatpak dependency set")
    # A release wheelhouse must never inherit obsolete artifacts from an
    # earlier resolution (especially accelerator-specific PyTorch wheels).
    if WHEELS.exists():
        shutil.rmtree(WHEELS)
    WHEELS.mkdir(parents=True)
    exports = (
        (None, "requirements-core.lock"),
        ("cohere-local", "requirements-cohere-local.lock"),
    )
    for extra, destination in exports:
        command = [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(PACKAGING / destination),
        ]
        if extra:
            command.extend(("--extra", extra))
        run(*command)
        download_command = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--dest",
            str(WHEELS),
            "--requirement",
            str(PACKAGING / destination),
        ]
        if extra == "cohere-local":
            # uv.lock binds torch to this explicit index. pip still needs the
            # index URL to retrieve the locked +cpu wheel during cache prep.
            download_command.extend(("--extra-index-url", PYTORCH_CPU_INDEX))
        run(*download_command)
    build_source_wheels()
    build_application_wheel()
    generate_desktop_sources()
    validate_accelerator_sources()
    print(f"Prepared offline Flatpak wheelhouse at {WHEELS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
