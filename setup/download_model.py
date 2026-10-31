# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Fetch Small once; complete installations also work without the internet."""
import json
from pathlib import Path

MODEL_REPOSITORY = "Systran/faster-whisper-small"
MODEL_FOLDER = Path(__file__).resolve().parents[1] / "runtime" / "models" / "small"
REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def model_errors(folder=None, required_files=REQUIRED_FILES):
    folder = MODEL_FOLDER if folder is None else folder
    errors = []
    for name in required_files:
        path = folder / name
        try:
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"Missing or empty: {name}")
            elif name.endswith(".json"):
                content = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(content, dict) or not content:
                    errors.append(f"Invalid model metadata: {name}")
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot read {name}: {exc}")
    return errors


def main():
    if not model_errors():
        print(f"Small is already installed. No download needed.\n{MODEL_FOLDER}")
        return 0
    print(f"Downloading multilingual Small (about 484 MB) to:\n{MODEL_FOLDER}")
    print("Keep this window open. Retrying resumes cached downloads.")
    try:
        from huggingface_hub import snapshot_download, hf_hub_download
        MODEL_FOLDER.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=MODEL_REPOSITORY, local_dir=str(MODEL_FOLDER),
                          allow_patterns=list(REQUIRED_FILES))
        # A local cache may still regard a damaged file as current. Repair only
        # files that fail validation, keeping the already-downloaded weights.
        for name in REQUIRED_FILES:
            if model_errors(required_files=(name,)):
                hf_hub_download(repo_id=MODEL_REPOSITORY, filename=name,
                                local_dir=str(MODEL_FOLDER), force_download=True)
        errors = model_errors()
        if errors:
            raise RuntimeError("; ".join(errors))
    except Exception as exc:
        print(f"Model setup failed: {exc}")
        print("Run Get Libraries if needed; check the connection, then retry Get Model and Start.")
        return 1
    print("Small is ready. Normal dictation now works offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
