# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Create a private local token for the optional backend on this computer."""

import json
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "app" / "backend" / "local_config.json"


def main() -> None:
    """Create the config only when it is missing or has a placeholder token."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    current = {}
    if CONFIG_PATH.is_file():
        try:
            current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}

    if not isinstance(current, dict):
        current = {}

    token = current.get("token", "")
    if not token or token == "GENERATED_BY_INSTALLER":
        current["token"] = secrets.token_urlsafe(32)

    if not isinstance(current.get("allowed_origins"), list):
        current["allowed_origins"] = []

    CONFIG_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("Private backend configuration is ready.")
    print("The local access token is stored only on this computer.")


if __name__ == "__main__":
    main()
