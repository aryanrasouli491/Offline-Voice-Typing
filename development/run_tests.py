# Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
"""Run unit tests from any working directory without importing user preferences."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "setup"), str(ROOT / "development")]

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(ROOT / "development" / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
