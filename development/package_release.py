# Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
"""Build the reviewed 2.0.0 source bundle, never local data, models or libraries."""
import hashlib
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from app_info import APP_VERSION

ROOT_FILES = ("1 - Get Project.cmd", "2 - Get Libraries.cmd", "3 - Get Model and Start.cmd",
              "README.md", "LICENSE", ".gitignore")


def source_files(root=ROOT):
    files = [root / name for name in ROOT_FILES]
    for folder in ("app", "setup", "development"):
        for path in (root / folder).rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in ("__pycache__", "artifacts", "fixtures", ".venv", ".git") for part in relative.parts):
                continue
            if path.name == "local_config.json":
                continue
            allowed = path.suffix in (".py", ".ps1", ".vbs")
            allowed |= folder == "setup" and path.name in ("requirements.txt", "requirements-api.txt")
            allowed |= relative.as_posix() == "app/backend/local_config.example.json"
            allowed |= relative.parts[:2] == ("app", "assets") and path.suffix == ".png"
            if allowed:
                files.append(path)
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(f"Missing release file: {path}")
    return sorted(files)


def build_archive(destination, root=ROOT):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = source_files(root)
    # Write a staging file so an interrupted build never leaves a valid-looking ZIP.
    temporary = destination.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, f"Offline-Voice-Typing-{APP_VERSION}/{path.relative_to(root).as_posix()}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    path = build_archive(ROOT / ".build" / f"NR-Voice-Typing-{APP_VERSION}-source.zip")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    print(f"Source-only release: {path}\nSHA256: {digest}")
