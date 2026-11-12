"""Installed storage paths and release privacy boundaries."""
import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app_paths import data_directory
import island_inline_watcher as app
from tools.check_bundle import check_bundle
from tools.package_source import validate_paths


class PackagingTests(unittest.TestCase):
    def test_frozen_data_is_independent_of_install_directory(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {"LOCALAPPDATA": folder}, clear=True), \
             patch.object(sys, "frozen", True, create=True):
            expected = Path(folder) / "IslandInlineWatcher"
            self.assertEqual(data_directory("/temporary/_internal/app.py"), expected)
            self.assertEqual(data_directory("/new-version/_internal/app.py"), expected)

    def test_source_keeps_existing_project_settings(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), \
             patch.object(sys, "frozen", False, create=True):
            self.assertEqual(data_directory(Path(folder) / "watcher.py"), Path(folder).resolve())

    def test_explicit_data_override_supports_isolated_checks(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {"ISLAND_DATA_DIR": folder}), \
             patch.object(sys, "frozen", True, create=True):
            self.assertEqual(data_directory("/bundled/app.py"), Path(folder).resolve())

    def test_first_save_creates_data_directory_and_preserves_other_settings(self):
        with TemporaryDirectory() as folder, patch.object(app, "SETTINGS_ENV", Path(folder) / "new" / ".env"):
            app.write_saved_settings("FIRST", {"text": "測試 $value"})
            app.write_saved_settings("SECOND", {"enabled": True})
            self.assertEqual(app.read_saved_settings("FIRST"), {"text": "測試 $value"})
            self.assertEqual(app.read_saved_settings("SECOND"), {"enabled": True})

    def test_runtime_imports_without_playwright(self):
        self.assertIsNone(importlib.util.find_spec("playwright"))
        from native_browser import BrowserError, BrowserTimeout
        self.assertIs(BrowserError, app.BrowserError)
        self.assertIs(BrowserTimeout, app.BrowserTimeout)

    def test_private_source_paths_are_rejected(self):
        for path in (".env", "folder/.env.backup", ".browser-native-profile/Cookies", "logs/query.jsonl", ".venv/python.exe"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_paths(["start.bat", path])
        validate_paths(["README.md", "tests/test_packaging.py", ".github/workflows/release.yml"])

    def test_bundle_rejects_private_data_and_playwright(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "IslandInlineWatcher.exe").touch()
            check_bundle(root)
            for path in (root / ".env", root / "playwright"):
                path.touch()
                with self.assertRaises(ValueError):
                    check_bundle(root)
                path.unlink()


if __name__ == "__main__":
    unittest.main()
