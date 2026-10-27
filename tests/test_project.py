"""Small checks for the distributable project layout."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProjectLayoutTests(unittest.TestCase):
    """Check that essential project files and safe defaults are present."""

    def test_desktop_app_exists(self):
        self.assertTrue((ROOT / "desktop_app.py").is_file())

    def test_model_downloader_targets_small_model(self):
        source = (ROOT / "download_model.py").read_text(encoding="utf-8")
        self.assertIn('"Systran/faster-whisper-small"', source)
        self.assertIn('"models" / "small"', source)

    def test_backend_binds_only_to_loopback(self):
        source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn('host="127.0.0.1"', source)

    def test_example_config_has_no_real_token(self):
        path = ROOT / "backend" / "local_config.example.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(config["token"], "GENERATED_BY_INSTALLER")

    def test_private_config_is_not_bundled(self):
        self.assertFalse((ROOT / "backend" / "local_config.json").exists())

    def test_installer_and_readme_exist(self):
        self.assertTrue((ROOT / "Install_Voice_Typing.bat").is_file())
        self.assertTrue((ROOT / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
