#!/usr/bin/env python3
"""Create the pinned offline wheelhouse used by Flatpak builds.

Run this only in a controlled release-preparation environment. The resulting
wheelhouse is an input to the offline builder; CI never asks pip to resolve or
download dependencies while it builds the application.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging" / "flatpak"
WHEELS = PACKAGING / "wheels"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
FLATPAK_PYTHON = (3, 13)
SETUPTOOLS_VERSION = "83.0.0"


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


def main() -> int:
    running_python = sys.version_info[:2]
    if running_python != FLATPAK_PYTHON:
        expected = ".".join(map(str, FLATPAK_PYTHON))
        actual = ".".join(map(str, running_python))
        raise SystemExit(
            f"Flatpak wheels must be prepared with Python {expected} to match "
            f"org.kde.Sdk//6.11 (running Python {actual})"
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
    print(f"Prepared offline Flatpak wheelhouse at {WHEELS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
