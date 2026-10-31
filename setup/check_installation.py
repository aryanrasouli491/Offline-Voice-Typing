# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Read-only installation check; does not open a microphone or download a model."""
import importlib
import sys
from download_model import model_errors


def main():
    errors = []
    for module in ("tkinter", "numpy", "sounddevice", "faster_whisper", "av"):
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"{module}: {exc}")
    errors.extend(model_errors())
    if errors:
        print("\n".join(errors))
        return 1
    print("Desktop dependencies and local Small model are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
