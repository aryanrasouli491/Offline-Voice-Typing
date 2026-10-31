# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Paths are relative to this installation, never to the caller's working folder."""
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
RUNTIME_DIR = ROOT / "runtime"
DEFAULT_MODELS_DIR = RUNTIME_DIR / "models"
DATA_DIR = RUNTIME_DIR / "data"
