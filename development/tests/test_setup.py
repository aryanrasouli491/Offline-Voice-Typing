# Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
"""Installation regressions use local archives and mocks; never access the network."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import download_model
import install_libraries
from package_release import source_files, build_archive, ROOT


class LibraryTests(unittest.TestCase):
    def test_compatible_installed_packages_are_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            requirements = Path(folder) / "requirements.txt"
            requirements.write_text("# ignored\nnumpy>=1.26,<3\nav>=11,<19\n", encoding="utf-8")
            with patch.object(install_libraries.metadata, "version", side_effect=["2.2.0", "18.1.0"]):
                self.assertEqual(install_libraries.missing_requirements(requirements), [])
            with patch.object(install_libraries.metadata, "version", side_effect=["1.0.0", "18.1.0"]):
                self.assertEqual(len(install_libraries.missing_requirements(requirements)), 1)

    def test_installed_environment_does_not_call_pip_install(self):
        with patch.object(install_libraries, "missing_requirements", return_value=[]), \
                patch.object(install_libraries, "check_imports", return_value=[]), \
                patch.object(install_libraries.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(install_libraries.main([]), 0)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-2:], ["pip", "check"])

    def test_check_only_never_installs_missing_packages(self):
        with patch.object(install_libraries, "missing_requirements", return_value=["av"]), \
                patch.object(install_libraries.subprocess, "run") as run:
            self.assertEqual(install_libraries.main(["--check"]), 1)
            run.assert_not_called()

    def test_model_check_rejects_empty_or_malformed_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name in download_model.REQUIRED_FILES:
                (folder / name).write_text('{"valid": true}' if name.endswith(".json") else "content")
            self.assertEqual(download_model.model_errors(folder), [])
            (folder / "model.bin").write_bytes(b"")
            (folder / "config.json").write_text("broken")
            self.assertEqual(len(download_model.model_errors(folder)), 2)

    def test_complete_model_does_not_contact_huggingface(self):
        with patch.object(download_model, "model_errors", return_value=[]), \
                patch("huggingface_hub.snapshot_download") as download:
            self.assertEqual(download_model.main(), 0)
            download.assert_not_called()

    def test_corrupt_cached_metadata_is_repaired_without_downloading_weights_again(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name in download_model.REQUIRED_FILES:
                (folder / name).write_text('{"valid": true}' if name.endswith(".json") else "weights")
            (folder / "config.json").write_text("broken")
            def repair(**kwargs):
                (folder / kwargs["filename"]).write_text('{"valid": true}')
            with patch.object(download_model, "MODEL_FOLDER", folder), \
                    patch("huggingface_hub.snapshot_download"), \
                    patch("huggingface_hub.hf_hub_download", side_effect=repair) as fetch:
                self.assertEqual(download_model.main(), 0)
                fetch.assert_called_once()
                self.assertEqual(fetch.call_args.kwargs["filename"], "config.json")


class ReleaseTests(unittest.TestCase):
    def test_source_bundle_excludes_personal_and_generated_files(self):
        paths = [p.relative_to(ROOT).as_posix() for p in source_files()]
        self.assertIn("app/desktop_app.py", paths)
        self.assertIn("app/backend/local_config.example.json", paths)
        for path in paths:
            self.assertFalse(any(part in path.split("/") for part in
                                 ("runtime", ".venv", "__pycache__", "artifacts", "fixtures", "local_config.json")), path)

    @unittest.skipUnless(os.name == "nt", "Windows downloader")
    def test_bootstrap_extracts_unicode_path_and_rejects_overwrite_and_traversal(self):
        # Exercise the exact PowerShell payload embedded in the one-file downloader.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            payload = (ROOT / "1 - Get Project.cmd").read_text().split("\n# POWERSHELL_PAYLOAD\n", 1)[1]
            script = folder / "bootstrap.ps1"
            script.write_text(payload, encoding="utf-8")
            archive = build_archive(folder / "source.zip")
            target = folder / "پروژه من"
            command = ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", str(script), "-ArchivePath", str(archive), "-Destination", str(target)]
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((target / "app" / "desktop_app.py").is_file())
            self.assertFalse((target / "runtime").exists())
            sentinel = target / "keep.txt"
            sentinel.write_text("do not overwrite")
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), "do not overwrite")
            with zipfile.ZipFile(archive, "w") as bad:
                bad.writestr("release/../../escaped.txt", "invalid")
            command[-1] = str(folder / "bad-target")
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((folder / "escaped.txt").exists())
            self.assertFalse((folder / "bad-target").exists())
