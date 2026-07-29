#!/usr/bin/env python3
"""Deterministic license-inventory gate for bundled Flatpak inputs.

It validates that every top-level packaged Python dependency is pinned in the
lock, known to the human-reviewed inventory, and (when installed locally) has
license metadata. It also requires entries for the native llama.cpp binary and
on-demand Qwen model. This intentionally makes a newly bundled dependency a
review failure instead of silently expanding the shipped license surface.
"""

from __future__ import annotations

import re
import sys
import zipfile
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "uv.lock"
INVENTORY = Path(__file__).with_name("LICENSES.md")
REQUIREMENTS = (
    Path(__file__).with_name("requirements-core.txt"),
    Path(__file__).with_name("requirements-cohere-local.txt"),
)
REQUIRED_NON_PYTHON = ("llama.cpp", "Qwen3-4B GGUF Q4_K_M", "Apache-2.0")


def requirement_names() -> dict[str, str]:
    requirements: dict[str, str] = {}
    for path in REQUIREMENTS:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.!+-]+)", line)
            if match is None:
                raise ValueError(f"{path.name}: dependency must use an exact == pin: {line}")
            requirements[match.group(1).casefold().replace("_", "-")] = line
    return requirements


def metadata_has_license(package: str) -> bool | None:
    try:
        package_metadata = metadata(package)
    except PackageNotFoundError:
        # Optional-extension packages are not installed in the source test
        # environment. Their lock/inventory entries still remain mandatory.
        return None
    expressions = [
        package_metadata.get("License", ""),
        package_metadata.get("License-Expression", ""),
        *package_metadata.get_all("Classifier", []),
    ]
    return any("license" in value.casefold() or value.strip() for value in expressions)


def main() -> int:
    requirements = requirement_names()
    lock = LOCK.read_text(encoding="utf-8")
    inventory = INVENTORY.read_text(encoding="utf-8")
    errors: list[str] = []
    for normalized, pin in requirements.items():
        lock_name = f'name = "{normalized}"'
        # uv normalizes underscore names but preserves the canonical hyphenated
        # package spelling for all current Flatpak top-level dependencies.
        if lock_name not in lock:
            errors.append(f"{pin} is absent from uv.lock")
        if pin not in inventory:
            errors.append(f"{pin} is absent from packaging/flatpak/LICENSES.md")
        has_license = metadata_has_license(pin.split("==", 1)[0])
        if has_license is False:
            errors.append(f"{pin} has no license field/classifier in installed metadata")
    for entry in REQUIRED_NON_PYTHON:
        if entry not in inventory:
            errors.append(f"inventory is missing required native/model entry: {entry}")
    if "--wheelhouse" in sys.argv:
        wheelhouse = Path(__file__).with_name("wheels")
        wheels = sorted(wheelhouse.glob("*.whl"))
        if not wheels:
            errors.append("the prepared wheelhouse is empty")
        for wheel in wheels:
            try:
                with zipfile.ZipFile(wheel) as archive:
                    metadata_name = next(
                        name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
                    )
                    package_metadata = archive.read(metadata_name).decode("utf-8", "replace")
            except (OSError, zipfile.BadZipFile, StopIteration):
                errors.append(f"{wheel.name} has no readable wheel metadata")
                continue
            declared = any(
                line.startswith(("License-Expression:", "License-File:"))
                or line.startswith("Classifier: License ::")
                or (line.startswith("License:") and line.removeprefix("License:").strip())
                for line in package_metadata.splitlines()
            )
            if not declared:
                errors.append(f"{wheel.name} has no declared license metadata")
    if errors:
        print("Flatpak license inventory check failed:", *errors, sep="\n- ", file=sys.stderr)
        return 1
    suffix = " and prepared wheels" if "--wheelhouse" in sys.argv else ""
    print(f"Flatpak license inventory is complete for pinned top-level inputs{suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
