# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Reuse compatible packages and install only missing/incompatible desktop wheels."""
import argparse
import importlib
import io
from importlib import metadata
from pathlib import Path
import struct
import subprocess
import sys
import wave

REQUIREMENTS = Path(__file__).with_name("requirements.txt")
MODULES = ("tkinter", "numpy", "sounddevice", "faster_whisper", "av", "huggingface_hub", "requests")


def missing_requirements(path=REQUIREMENTS):
    # pip ships its own version parser; no extra bootstrap dependency is needed.
    from pip._vendor.packaging.requirements import Requirement
    missing = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            version = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            missing.append(str(requirement))
        else:
            if not requirement.specifier.contains(version, prereleases=True):
                missing.append(str(requirement))
    return missing


def check_imports():
    errors = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    if not errors:
        try:
            from faster_whisper.audio import decode_audio
            data = io.BytesIO()
            with wave.open(data, "wb") as writer:
                writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                writer.writeframes(b"\x00\x00" * 1600)
            data.seek(0)
            if len(decode_audio(data)) != 1600:
                errors.append("The audio decoder returned incomplete samples.")
        except Exception as exc:
            errors.append(f"Audio decoder compatibility: {exc}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only; no downloads or changes")
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        print("Use Python 3.12 (64-bit). The existing environment was left unchanged.")
        return 1
    missing = missing_requirements()
    if missing:
        print("Missing or incompatible packages: " + ", ".join(missing))
        if args.check:
            return 1
        # No upgrade or force-reinstall: pip retains compatible installed versions,
        # resolves their dependencies, and uses its wheel cache on repeat runs.
        result = subprocess.run([sys.executable, "-m", "pip", "install",
                                 "--disable-pip-version-check", "--only-binary=:all:",
                                 "-r", str(REQUIREMENTS)], check=False)
        if result.returncode:
            print("Check the connection and retry Get Libraries. Compatible packages are kept.")
            return result.returncode
        remaining = missing_requirements()
        if remaining:
            print("Requirements are still unresolved after installation: " + ", ".join(remaining))
            return 1
    else:
        print("All required package versions are present. No package downloads needed.")
    # Metadata alone cannot reveal a broken binary import or transitive dependency.
    checked = subprocess.run([sys.executable, "-m", "pip", "check"], check=False)
    errors = check_imports()
    if checked.returncode or errors:
        print("\n".join(errors))
        print("Library verification failed. See README > Troubleshooting; no user data was changed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
