"""Download the Faster-Whisper Small model into this project's models folder."""

from pathlib import Path
import sys

from huggingface_hub import snapshot_download


# This is the public upstream model repository maintained for Faster-Whisper.
MODEL_REPOSITORY = "Systran/faster-whisper-small"
PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_FOLDER = PROJECT_ROOT / "models" / "small"
REQUIRED_FILES = (
    "config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.txt",
)


def main() -> int:
    """Download only the files needed by the local transcription app."""
    MODEL_FOLDER.mkdir(parents=True, exist_ok=True)

    if all((MODEL_FOLDER / name).is_file() for name in REQUIRED_FILES):
        print("The Faster-Whisper Small model is already present.")
        print(f"Model folder: {MODEL_FOLDER}")
        return 0

    print("Downloading Faster-Whisper Small from Hugging Face.")
    print("The main model file is about 484 MB. A stable internet connection is needed.")
    print(f"Destination: {MODEL_FOLDER}")
    print("Please keep this window open until the download finishes.\n")

    try:
        snapshot_download(
            repo_id=MODEL_REPOSITORY,
            local_dir=str(MODEL_FOLDER),
            allow_patterns=[
                "config.json",
                "model.bin",
                "tokenizer.json",
                "vocabulary.txt",
                "vocabulary.json",
                "preprocessor_config.json",
            ],
        )
    except Exception as exc:
        print("\nModel download failed.")
        print("Check your internet connection and try Install_Voice_Typing.bat again.")
        print("The download can usually resume from cached files.")
        print(f"Technical details: {exc}")
        return 1

    missing_files = [name for name in REQUIRED_FILES if not (MODEL_FOLDER / name).is_file()]
    if missing_files:
        print("\nThe model download finished, but some files are missing:")
        for name in missing_files:
            print(f"  - {name}")
        print("Run the installer again to retry the download.")
        return 2

    print("\nModel download completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
