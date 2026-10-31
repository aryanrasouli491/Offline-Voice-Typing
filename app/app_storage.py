# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Validated preferences and backward-compatible local transcript history."""
from __future__ import annotations
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from project_paths import ROOT, DEFAULT_MODELS_DIR

DEFAULTS = {
    "settings_version": 7, "theme": "dark", "language": "en",
    "width": 1000, "height": 720, "live": False, "auto_paste": True,
    "silence_seconds": 2.0, "threshold": 0.008, "overlay": True,
    "background_on_close": True, "microphone": "Default microphone",
    "hotkey": "Ctrl+Alt+Space", "quality": "Balanced", "save_history": True,
    "insertion_method": "Clipboard paste",
    "overlay_position": None,
}


def load_settings(path: Path) -> dict:
    settings = dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return settings
    except (OSError, ValueError):
        return settings
    for key in ("live", "auto_paste", "overlay", "background_on_close", "save_history"):
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    for key, choices in {
        "theme": ("dark", "light"), "language": ("en", "fa"),
        "hotkey": ("Ctrl+Alt+Space", "Ctrl+Shift+Space", "Ctrl+Alt+D"),
        "quality": ("Fast", "Balanced", "Accurate"),
        "insertion_method": ("Clipboard paste", "Unicode typing"),
    }.items():
        if raw.get(key) in choices:
            settings[key] = raw[key]
    for key, low, high in (("width", 760, 1600), ("height", 580, 1000),
                           ("silence_seconds", 0.8, 8), ("threshold", 0.002, 0.08)):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            settings[key] = max(low, min(high, value))
    for key in ("models_dir", "selected_model_key", "microphone"):
        if isinstance(raw.get(key), str):
            settings[key] = raw[key]
    # Preserve the model selection when upgrading the old flat folder layout.
    for key in ("models_dir", "selected_model_key"):
        if settings.get(key):
            try:
                relative = Path(settings[key]).relative_to(ROOT / "models")
            except ValueError:
                continue
            settings[key] = str(DEFAULT_MODELS_DIR / relative)
    position = raw.get("overlay_position")
    if (isinstance(position, list) and len(position) == 2
            and all(type(v) is int and -100000 <= v <= 100000 for v in position)):
        settings["overlay_position"] = position
    return settings


def save_settings(path: Path, settings: dict) -> None:
    """Atomic replacement avoids half-written preferences after a power loss."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class History:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                filename TEXT NOT NULL, model_name TEXT NOT NULL, transcript TEXT NOT NULL)""")

    @contextmanager
    def connect(self):
        # sqlite's own context manager commits, but does not close the handle.
        connection = sqlite3.connect(self.path, timeout=3)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, source: str, model: str, text: str):
        if text.strip():
            with self.connect() as conn:
                conn.execute("INSERT INTO history(created_at, filename, model_name, transcript) VALUES (?, ?, ?, ?)",
                             (datetime.now().isoformat(timespec="seconds"), source, model, text))

    def search(self, query="", limit=200):
        with self.connect() as conn:
            return conn.execute("SELECT id, created_at, filename, model_name, transcript FROM history "
                                "WHERE transcript LIKE ? ORDER BY id DESC LIMIT ?",
                                (f"%{query}%", limit)).fetchall()

    def delete(self, row_id: int):
        with self.connect() as conn:
            conn.execute("DELETE FROM history WHERE id=?", (row_id,))

    def delete_many(self, row_ids):
        """Delete the selected rows in one transaction and ignore duplicates."""
        ids = sorted({row_id for row_id in row_ids if type(row_id) is int and row_id > 0})
        if ids:
            with self.connect() as conn:
                conn.executemany("DELETE FROM history WHERE id=?", ((row_id,) for row_id in ids))
