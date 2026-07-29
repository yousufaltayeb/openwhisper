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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging" / "flatpak"
WHEELS = PACKAGING / "wheels"


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    if shutil.which("uv") is None:
        raise SystemExit("uv is required to export the locked Flatpak dependency set")
    WHEELS.mkdir(parents=True, exist_ok=True)
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
        run(
            sys.executable,
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--dest",
            str(WHEELS),
            "--requirement",
            str(PACKAGING / destination),
        )
    print(f"Prepared offline Flatpak wheelhouse at {WHEELS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
